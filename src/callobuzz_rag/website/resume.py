"""Resume sites whose reading stopped at a daily fair-use limit (PIP3).

A site read that ran into the business's pages-per-day or fetches-per-day limit ends cleanly
with `counts.limited_by` set to `daily_limit` or `fetch_limit` (see `website/learn.py`); nothing
it had not read yet was dropped, only delayed. Hourly, this finds the sites whose LAST run ended
that way on an earlier UTC day and starts a full read again, so a big site is learned a slice a
day without anyone remembering to press a button. A site that is paused or blocked, or whose
workspace is not active, is left alone (it resumes with the workspace).
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import aliased

from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.website import WebsiteRun, WebsiteSource

logger = logging.getLogger(__name__)

LIMITED_BY = ("daily_limit", "fetch_limit")
LOOKBACK = timedelta(days=7)


async def resume_limited(
    session_factory: Callable[[], Any], *, dispatch_learn: Callable[..., Any], now: datetime
) -> list[str]:
    """Start `full` reads for every site whose last discovery run was cut short by a daily
    limit before today (UTC). Returns the source ids it started."""
    moment = now.astimezone(UTC)
    day_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    later = aliased(WebsiteRun)
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(WebsiteRun.source_id)
                .join(WebsiteSource, WebsiteSource.id == WebsiteRun.source_id)
                .join(Tenant, Tenant.id == WebsiteSource.tenant_id)
                .where(
                    WebsiteRun.finished_at.is_not(None),
                    WebsiteRun.finished_at < day_start,
                    WebsiteRun.finished_at >= moment - LOOKBACK,
                    WebsiteRun.kind.in_(("first", "full")),
                    WebsiteRun.counts["limited_by"].astext.in_(LIMITED_BY),
                    WebsiteSource.status.notin_(("paused", "blocked", "discovering", "learning")),
                    Tenant.status == "active",
                    ~exists().where(
                        and_(
                            later.source_id == WebsiteRun.source_id,
                            later.started_at > WebsiteRun.started_at,
                            # a freshness check or a push in between is not a re-read of the site
                            later.kind.in_(("first", "full")),
                        )
                    ),
                )
                .distinct()
            )
        ).all()
        started: list[str] = []
        for (source_id,) in rows:
            source = await session.get(WebsiteSource, source_id)
            if source is None:
                continue
            source.status, source.pause_reason = "discovering", None
            started.append(str(source_id))
        await session.commit()
    for source_id in started:
        dispatch_learn(source_id, "full", "schedule")
    if started:
        logger.info("Resumed %d website reads cut short by a daily limit", len(started))
    return started
