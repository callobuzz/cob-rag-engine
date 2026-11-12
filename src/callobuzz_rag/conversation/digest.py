"""Daily lead digest (design 4.7, notify "digest daily"; conversation C9).

Runs hourly. A knowledge base is due once its business timezone's local hour has reached
RAG_LEAD_DIGEST_HOUR; the digest covers the previous local calendar day. A Redis claim per
(knowledge base, day) keeps it to one mail a day, so a missed run (worker down, Redis down) is
made up by the next hourly run. With Redis unreachable the knowledge base is skipped (fail
closed: a missed digest beats a duplicate mail with contact data).

Only what the templates print goes into the mail context (it transits the Redis broker).
Knowledge bases of tenants that are not `active` are skipped: this task bypasses the request
middleware that enforces tenant status.
"""

import logging
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

MAX_ROWS = 50
CLAIM_TTL_S = 172800


@dataclass(frozen=True)
class DigestRow:
    name: str | None
    phone: str | None
    status: str
    qualification: str | None
    created_local: str


def is_due(tz: str, now: datetime, hour: int) -> bool:
    """True from `hour` local time to midnight; the claim keeps it to once a day."""
    return now.astimezone(ZoneInfo(tz)).hour >= hour


def digest_window(tz: str, now: datetime) -> tuple[datetime, datetime, date]:
    """The previous local calendar day as a UTC range, plus that local date."""
    zone = ZoneInfo(tz)
    day = now.astimezone(zone).date() - timedelta(days=1)
    start = datetime.combine(day, time(0), tzinfo=zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=zone).astimezone(UTC)
    return start, end, day


def build_digest(rows: list[DigestRow]) -> dict[str, Any]:
    return {
        "total": len(rows),
        "by_status": dict(Counter(r.status for r in rows)),
        "by_qualification": dict(Counter(r.qualification or "none" for r in rows)),
        "rows": [asdict(r) for r in rows[:MAX_ROWS]],
        "more": max(0, len(rows) - MAX_ROWS),
    }


def _key(corpus_id: str, day: date) -> str:
    return f"digest:{corpus_id}:{day.isoformat()}"


async def claim(redis: Any, corpus_id: str, day: date) -> bool:
    try:
        return bool(await redis.set(_key(corpus_id, day), "1", nx=True, ex=CLAIM_TTL_S))
    except Exception:
        logger.warning("Digest claim failed for %s (Redis unreachable)", corpus_id, exc_info=True)
        return False


async def release(redis: Any, corpus_id: str, day: date) -> None:
    try:
        await redis.delete(_key(corpus_id, day))
    except Exception:
        logger.warning("Could not release the digest claim for %s", corpus_id, exc_info=True)


def _row_of(crypto: Any, lead: Any, zone: ZoneInfo) -> tuple[DigestRow, bool]:
    from callobuzz_rag.conversation.leads.service import lead_fields

    failed = False
    try:
        fields = lead_fields(crypto, lead)
    except Exception:
        fields, failed = {}, True
    row = DigestRow(
        name=fields.get("name"),
        phone=fields.get("phone"),
        status=lead.status,
        qualification=lead.qualification,
        created_local=lead.created_at.astimezone(zone).strftime("%H:%M"),
    )
    return row, failed


async def send_digests(
    session_factory: Any, settings: Any, redis: Any, now: datetime
) -> dict[str, int]:
    from sqlalchemy import select

    from callobuzz_rag.accounts.email.dispatch import enqueue_email
    from callobuzz_rag.accounts.email.render import dashboard_link
    from callobuzz_rag.conversation.leads.crypto import (
        LeadCryptoUnavailableError,
        lead_crypto_from_settings,
    )
    from callobuzz_rag.conversation.schema import load_config
    from callobuzz_rag.core.exceptions import AssistantSchemaTooNewError
    from callobuzz_rag.db.models.assistant_config import (
        STATUS_PUBLISHED,
        AssistantConfigVersion,
    )
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.lead import Lead
    from callobuzz_rag.db.models.tenant import Tenant
    from callobuzz_rag.worker.conversation_tasks import LEAD_EMAIL_EXPIRES_S

    try:
        crypto = lead_crypto_from_settings(settings)
    except LeadCryptoUnavailableError:
        logger.warning("Lead digest skipped: RAG_ENCRYPTION_KEY is not set")
        return {"sent": 0, "skipped": 0}
    sent = skipped = 0
    async with session_factory() as session:
        digest_on = AssistantConfigVersion.config["leads"]["notify"]["daily_digest"].as_boolean()
        published = (
            await session.execute(
                select(AssistantConfigVersion, Corpus)
                .join(Corpus, Corpus.id == AssistantConfigVersion.corpus_id)
                .join(Tenant, Tenant.id == Corpus.tenant_id)
                .where(
                    AssistantConfigVersion.status == STATUS_PUBLISHED,
                    digest_on.is_(True),
                    Tenant.status == "active",
                )
            )
        ).all()
        for record, corpus in published:
            try:
                config = load_config(record.config)
            except AssistantSchemaTooNewError:
                logger.warning("Digest: assistant of %s is saved by a newer engine", corpus.id)
                continue
            notify = config.leads.notify
            tz = config.identity.card.timezone
            if not (config.enabled and config.leads.enabled and notify.emails):
                continue
            if not is_due(tz, now, settings.lead_digest_hour):
                continue
            start, end, day = digest_window(tz, now)
            # Claim first so later hourly runs for a done day never query leads again.
            if not await claim(redis, str(corpus.id), day):
                skipped += 1
                continue
            try:
                leads = (
                    (
                        await session.execute(
                            select(Lead)
                            .where(
                                Lead.corpus_id == corpus.id,
                                Lead.created_at >= start,
                                Lead.created_at < end,
                            )
                            .order_by(Lead.created_at)
                        )
                    )
                    .scalars()
                    .all()
                )
            except Exception:
                await release(redis, str(corpus.id), day)
                raise
            if not leads:
                continue  # claimed on purpose: later hours do not query again
            zone = ZoneInfo(tz)
            built = [_row_of(crypto, lead, zone) for lead in leads]
            unreadable = sum(1 for _, failed in built if failed)
            if unreadable:
                logger.warning("Digest: %s leads of %s are unreadable", unreadable, corpus.id)
            context = {
                **build_digest([row for row, _ in built]),
                "business_name": config.identity.business_name or corpus.name,
                "kb_name": corpus.name,
                "day": f"{day.day} {day:%b %Y}",
                "leads_url": dashboard_link(settings.dashboard_url, f"leads?kb={corpus.id}"),
            }
            try:
                for to in notify.emails:
                    enqueue_email("lead_digest", to, context, expires_s=LEAD_EMAIL_EXPIRES_S)
            except Exception:
                logger.error("Digest e-mail for %s could not be queued", corpus.id, exc_info=True)
                await release(redis, str(corpus.id), day)
                continue
            sent += 1
    return {"sent": sent, "skipped": skipped}
