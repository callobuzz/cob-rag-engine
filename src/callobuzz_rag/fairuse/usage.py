"""How much of each limit a business has used (PIP3).

`read_usage` reads ONLY the metrics it is asked for (the gate asks for the limited ones), so a
business without limits costs no query. Where each number comes from:

- questions per minute / hour: the Redis sliding windows (`counters.py`), fail open;
- questions per day: today's `usage_daily.requests`, every source except `cob`;
- COB questions per day: today's `usage_daily.requests` with source `cob`;
- AI spend per day / month: `usage_daily.cost_micro` where the deployer paid (`payer =
  operator`), fail CLOSED (`spend_unknown`: a failed read must never mean "nothing spent");
- pages per day, fetches per day, uploads per day: `fair_use_counters`;
- pages in total: the pages the business's websites hold; storage: the bytes of its documents
  and of its uploads still in flight.
"""

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import BigInteger, cast, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.db.models.usage import UsageDaily
from callobuzz_rag.db.models.usage_charge import UsageCharge
from callobuzz_rag.db.models.website import WebsiteSource
from callobuzz_rag.fairuse import counters

logger = logging.getLogger(__name__)

COB_SOURCE = "cob"


@dataclass(slots=True)
class FairUseUsage:
    questions_per_minute: int = 0
    questions_per_hour: int = 0
    questions_per_day: int = 0
    cob_questions_per_day: int = 0
    spend_day_usd_micro: int = 0
    spend_month_usd_micro: int = 0
    spend_cap_month_micro: int = 0  # TV3 spend cap: usage charged this month (account micro units)
    website_pages_per_day: int = 0
    website_pages_total: int = 0
    fetches_per_day: int = 0
    uploads_per_day: int = 0
    storage_bytes: int = 0
    spend_unknown: bool = False
    windows_unavailable: bool = False

    def used(self, metric: str) -> int:
        """The number to compare with the limit of `metric` (spend in USD micro, storage in MB
        rounded up so a business is never told it has room it does not have)."""
        if metric == "ai_spend_per_day":
            return self.spend_day_usd_micro
        if metric == "ai_spend_per_month":
            return self.spend_month_usd_micro
        if metric == "spend_cap_per_month":  # TV3 spend cap
            return self.spend_cap_month_micro
        if metric == "storage_mb":
            return -(-self.storage_bytes // (1024 * 1024))
        return int(getattr(self, metric, 0))


def _day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


async def read_usage(
    session: AsyncSession,
    redis: Any,
    tenant_id: uuid.UUID,
    now: datetime,
    *,
    metrics: Iterable[str],
) -> FairUseUsage:
    """The usage of `metrics` (and nothing else) for `tenant_id` at `now`."""
    wanted = set(metrics)
    usage = FairUseUsage()
    day = counters.utc_day(now)

    for window, attr in (("minute", "questions_per_minute"), ("hour", "questions_per_hour")):
        if attr in wanted:
            try:
                setattr(usage, attr, await counters.read_window(redis, tenant_id, window, now))
            except Exception:
                usage.windows_unavailable = True
                logger.warning(
                    "fair-use counters unavailable; %s not checked", attr, exc_info=False
                )

    if "questions_per_day" in wanted or "cob_questions_per_day" in wanted:
        rows = await session.execute(
            select(
                func.coalesce(
                    func.sum(UsageDaily.requests).filter(UsageDaily.source != COB_SOURCE), 0
                ),
                func.coalesce(
                    func.sum(UsageDaily.requests).filter(UsageDaily.source == COB_SOURCE), 0
                ),
            ).where(UsageDaily.tenant_id == tenant_id, UsageDaily.day == day)
        )
        answers, cob = rows.one()
        usage.questions_per_day, usage.cob_questions_per_day = int(answers), int(cob)

    if "ai_spend_per_day" in wanted or "ai_spend_per_month" in wanted:
        try:
            month_start = day.replace(day=1)
            # A savepoint: a failed read must not poison the request's transaction.
            async with session.begin_nested():
                rows = await session.execute(
                    select(
                        func.coalesce(
                            func.sum(UsageDaily.cost_micro).filter(UsageDaily.day == day), 0
                        ),
                        func.coalesce(func.sum(UsageDaily.cost_micro), 0),
                    ).where(
                        UsageDaily.tenant_id == tenant_id,
                        UsageDaily.payer == "operator",
                        UsageDaily.day >= month_start,
                        UsageDaily.day <= day,
                    )
                )
                spent_day, spent_month = rows.one()
            usage.spend_day_usd_micro = int(spent_day)
            usage.spend_month_usd_micro = int(spent_month)
        except Exception:
            usage.spend_unknown = True
            logger.warning("AI spend of %s could not be read; failing closed", tenant_id)

    if "spend_cap_per_month" in wanted:  # TV3 spend cap: only asked for when a cap is set
        try:
            async with session.begin_nested():
                charged = await session.execute(
                    select(func.coalesce(func.sum(UsageCharge.amount_micro), 0)).where(
                        UsageCharge.tenant_id == tenant_id,
                        UsageCharge.day >= day.replace(day=1),
                        UsageCharge.day <= day,
                    )
                )
                usage.spend_cap_month_micro = int(charged.scalar_one() or 0)
        except Exception:
            usage.spend_unknown = True  # fail closed, like the AI spend
            logger.warning("Usage charges of %s could not be read; failing closed", tenant_id)

    counter_names = {
        "website_pages_per_day": "pages_learned",
        "fetches_per_day": "fetches",
        "uploads_per_day": "uploads",
    }
    needed = {counter_names[m]: m for m in counter_names if m in wanted}
    if needed:
        values = await counters.read_counters(session, tenant_id, day, needed)
        for counter, attr in needed.items():
            setattr(usage, attr, values[counter])

    if "website_pages_total" in wanted:
        total = await session.execute(
            select(func.coalesce(func.sum(WebsiteSource.pages_learned), 0)).where(
                WebsiteSource.tenant_id == tenant_id
            )
        )
        usage.website_pages_total = int(total.scalar_one() or 0)

    if "storage_mb" in wanted:
        usage.storage_bytes = await storage_bytes(session, tenant_id)
    return usage


async def storage_bytes(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    """The bytes of every document of the business's knowledge bases PLUS the bytes of its
    uploads still in flight (`review_metadata.size_bytes` of a queued or running job): the
    document row, and its size, only exist once the worker has parsed the file, so without
    the second half twenty files dropped at once would all see an empty business.

    One statement, so a job that completes meanwhile is counted in one half or the other.
    A running job whose document row already exists is counted in both until it completes
    (a short, conservative overlap: never too little)."""
    from callobuzz_rag.accounts.caps import IN_FLIGHT_STATUSES, IN_FLIGHT_WINDOW

    stored = (
        select(func.coalesce(func.sum(Document.size_bytes), 0))
        .join(Corpus, Corpus.id == Document.corpus_id)
        .where(Corpus.tenant_id == tenant_id)
        .scalar_subquery()
    )
    pending = (
        select(
            func.coalesce(
                func.sum(cast(IngestJob.review_metadata["size_bytes"].as_string(), BigInteger)),
                0,
            )
        )
        .join(Corpus, Corpus.id == IngestJob.corpus_id)
        .where(
            Corpus.tenant_id == tenant_id,
            IngestJob.status.in_(IN_FLIGHT_STATUSES),
            IngestJob.created_at
            > func.now()
            - literal_column(f"interval '{int(IN_FLIGHT_WINDOW.total_seconds())} seconds'"),
            IngestJob.review_metadata["size_bytes"].as_string().op("~")("^[0-9]+$"),
        )
        .scalar_subquery()
    )
    total = await session.execute(select(stored + pending))
    return int(total.scalar_one() or 0)
