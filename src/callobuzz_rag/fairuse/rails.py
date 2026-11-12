"""Engine-wide safety rails (PIP3): the deployer's AI spend per day, and crawl concurrency.

**Spend.** `EngineSettings.pipelines.engine_ai_spend_per_day_usd_micro` is the most the
deployer's own key may spend in a UTC day across every business (BYOK answers cost the deployer
nothing and are not counted; the operator's and the system workspace's own use is not counted
either: they are the deployer). The sum is read at most every 30 seconds per process, so a busy
engine pays one query a half-minute for the rail. Reaching the ceiling is a hard breach that
`decide` turns into "answer on the free local model, or pause" for every operator-paid answer;
a failed read is the same (spend fails closed). The deployer is told once at the alert level
and once at the ceiling (`alerts.py`).

**Crawl.** `crawl_slot` makes website runs take one of `pipelines.crawl_concurrency` slots, the
same Redis-lease mechanism the answer lanes use (one mechanism for every engine-wide
concurrency limit).
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.usage import UsageDaily
from callobuzz_rag.fairuse.decision import resets_at
from callobuzz_rag.fairuse.limits import Breach
from callobuzz_rag.tiers.lanes import AnswerLanes, LaneConfig, LaneTimeout
from callobuzz_rag.tiers.slot import _keep_alive

logger = logging.getLogger(__name__)

SPEND_CACHE_S = 30.0
ENGINE_METRIC = "engine_ai_spend_per_day"
# A website run waits this long for a crawl slot, then goes back on the queue.
CRAWL_WAIT_S = 600.0
CRAWL_LEASE_S = 900

_spend_cache: dict[str, tuple[int | None, float]] = {}


def reset_cache() -> None:
    _spend_cache.clear()


async def engine_spend_today(session: AsyncSession, now: datetime) -> int:
    """USD micro the deployer's key spent today on businesses (not the operator's own)."""
    day = now.astimezone(UTC).date()
    row = await session.execute(
        select(func.coalesce(func.sum(UsageDaily.cost_micro), 0))
        .join(Tenant, Tenant.id == UsageDaily.tenant_id)
        .join(Plan, Plan.id == Tenant.plan_id)
        .where(
            UsageDaily.day == day,
            UsageDaily.payer == "operator",
            Tenant.is_operator.is_(False),
            Tenant.is_system.is_(False),
            Plan.is_internal.is_(False),
        )
    )
    return int(row.scalar_one())


async def _cached_spend(session: AsyncSession, now: datetime) -> int | None:
    """Today's engine spend through the 30-second cache; None when it could not be read."""
    key = now.astimezone(UTC).date().isoformat()
    cached = _spend_cache.get(key)
    if cached is not None and cached[1] > time.monotonic():
        return cached[0]
    value: int | None
    try:
        async with session.begin_nested():  # a failed read must not poison the transaction
            value = await engine_spend_today(session, now)
    except Exception:
        logger.warning("Engine AI spend could not be read; failing closed", exc_info=True)
        value = None
    _spend_cache.clear()
    _spend_cache[key] = (value, time.monotonic() + (SPEND_CACHE_S if value is not None else 5.0))
    return value


async def engine_breach(
    app: Any, session: AsyncSession, *, now: datetime | None = None
) -> Breach | None:
    """The engine rail's breach for an operator-paid answer, or None (no ceiling set, or well
    below it). `near` from the alert level, `hard` at the ceiling, `hard` and unknown when the
    spend could not be read."""
    from callobuzz_rag.accounts.settings_store import get_engine_settings

    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        return None
    try:
        rails = (await get_engine_settings(factory)).pipelines
    except Exception:
        logger.warning("Engine settings unreadable; engine spend rail not applied", exc_info=True)
        return None
    limit = rails.engine_ai_spend_per_day_usd_micro
    if limit is None:
        return None
    moment = now or datetime.now(UTC)
    reset = resets_at(ENGINE_METRIC, moment)
    spent = await _cached_spend(session, moment)
    if spent is None:
        return Breach(ENGINE_METRIC, "hard", limit, 0, reset, unknown=True)  # type: ignore[arg-type]
    if spent >= limit:
        return Breach(ENGINE_METRIC, "hard", limit, spent, reset)  # type: ignore[arg-type]
    if spent * 100 >= limit * rails.engine_spend_alert_pct:
        return Breach(ENGINE_METRIC, "near", limit, spent, reset)  # type: ignore[arg-type]
    return None


@contextlib.asynccontextmanager
async def crawl_slot(
    redis: Any,
    concurrency: int | None,
    *,
    tenant_id: uuid.UUID,
    wait_s: float = CRAWL_WAIT_S,
    poll_ms: tuple[int, int] = (500, 1500),
) -> AsyncIterator[bool]:
    """Hold one of `concurrency` crawl slots for the block (yields True), waiting up to `wait_s`
    (ten minutes). Yields True at once when there is no limit. Raises `LaneTimeout` when no slot
    came free: the website task puts the run back on the queue. Redis trouble never stops a
    crawl."""
    if not concurrency:
        yield True
        return
    lanes = AnswerLanes(
        redis,
        {
            "crawl": LaneConfig(
                slots=concurrency,
                reserve_high=0,
                low_max=concurrency,
                lease_s=CRAWL_LEASE_S,
                max_wait_s=wait_s,
                poll_ms=poll_ms,
            )
        },
    )
    lease = await lanes.acquire(
        pool="crawl", tenant_id=tenant_id, priority="normal", max_concurrency=None
    )
    keeper = asyncio.ensure_future(_keep_alive(lease)) if lease.keys else None
    try:
        yield True
    finally:
        if keeper is not None:
            keeper.cancel()
        await asyncio.shield(lease.release())


__all__ = ["LaneTimeout", "crawl_slot", "engine_breach", "engine_spend_today", "reset_cache"]
