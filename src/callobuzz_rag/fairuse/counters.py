"""What the engine counts for fair use: Redis windows for questions, DB rows for the rest.

Question windows (minute, hour) are Redis keys written by the gate when it answers. They use
the sliding estimate the widget limiter uses (this window plus the previous one, weighted by
how much of it still overlaps), fail OPEN (a Redis problem is logged and the answer goes ahead:
a rate counter must never take a business's answers down) and expire on their own.

Days and months are not counted here: they are read from `usage_daily` (see `usage.py`). What
only the engine can count (pages learned, fetches, uploads, the decisions it made) lives in
`fair_use_counters`, one row per business, UTC day and metric, written by an atomic upsert.
"""

import logging
import time
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from typing import Any, Final

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.db.models.fair_use import FairUseCounter

logger = logging.getLogger(__name__)

WINDOWS: Final[Mapping[str, int]] = {"minute": 60, "hour": 3600}
_RESERVE_TRIES: Final = 4
COUNTER_METRICS: Final[tuple[str, ...]] = (
    "pages_learned",
    "fetches",
    "uploads",
    "decisions_downgraded",
    "decisions_paused",
    "decisions_busy",
)


def window_key(tenant_id: uuid.UUID, window: str, n: int) -> str:
    return f"fu:q:{tenant_id}:{window[0]}:{n}"


def _epoch(now: datetime | None) -> float:
    return now.timestamp() if now is not None else time.time()


async def hit_question(redis: Any, tenant_id: uuid.UUID, now: datetime | None = None) -> bool:
    """Count one answered question in the minute and hour windows. False (and a warning)
    when Redis is down; the answer goes ahead either way."""
    at = _epoch(now)
    try:
        pipe = redis.pipeline()
        for window, seconds in WINDOWS.items():
            key = window_key(tenant_id, window, int(at // seconds))
            pipe.incr(key)
            pipe.expire(key, seconds * 2, nx=True)
        await pipe.execute()
    except Exception:
        logger.warning("fair-use counters unavailable; question not counted", exc_info=True)
        return False
    return True


async def unhit_question(redis: Any, tenant_id: uuid.UUID, now: datetime | None = None) -> bool:
    """Take back one question counted by `hit_question`: the answer never happened (the visitor
    got the busy reply). `now` is when it is taken back; the count comes out of the current
    bucket, or the previous one when the wait crossed the boundary, and never goes below zero.
    False (and a warning) when Redis is down: the question simply stays counted."""
    at = _epoch(now)
    try:
        for window, seconds in WINDOWS.items():
            current = int(at // seconds)
            here = window_key(tenant_id, window, current)
            before = window_key(tenant_id, window, current - 1)
            count, previous = await redis.mget(here, before)
            if int(count or 0) > 0:
                await redis.incrby(here, -1)
            elif int(previous or 0) > 0:
                await redis.incrby(before, -1)
    except Exception:
        logger.warning("fair-use counters unavailable; busy question stays counted", exc_info=True)
        return False
    return True


async def read_window(
    redis: Any, tenant_id: uuid.UUID, window: str, now: datetime | None = None
) -> int:
    """The sliding estimate of questions in the last `window` (not counting a question that
    has not been answered yet). Raises when Redis fails: the caller decides to fail open."""
    seconds = WINDOWS[window]
    at = _epoch(now)
    current = int(at // seconds)
    elapsed = at - current * seconds
    count, previous = await redis.mget(
        window_key(tenant_id, window, current), window_key(tenant_id, window, current - 1)
    )
    weighted = int(count or 0) + int(previous or 0) * (1 - elapsed / seconds)
    return int(weighted)


def utc_day(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(UTC).date()


async def bump(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    metric: str,
    n: int = 1,
    day: date | None = None,
) -> None:
    """Add `n` to a day counter (atomic upsert). The caller commits."""
    assert metric in COUNTER_METRICS, f"unknown fair-use counter {metric!r}"
    if n == 0:
        return
    stmt = insert(FairUseCounter).values(
        tenant_id=tenant_id, day=day or utc_day(), metric=metric, value=n
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["tenant_id", "day", "metric"],
            set_={"value": FairUseCounter.value + stmt.excluded.value},
        )
    )


async def reserve(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    metric: str,
    n: int,
    limit: int,
    day: date | None = None,
) -> int:
    """Take up to `n` from a day counter without ever passing `limit`; returns what was taken.
    The caller commits.

    The upsert only applies where the new value still fits (`WHERE value + n <= limit`), so
    two runs of one business racing for the last units cannot both get them: PostgreSQL
    re-checks the condition against the committed row after waiting for the row lock. A race
    that lost its units is retried with what is left."""
    assert metric in COUNTER_METRICS, f"unknown fair-use counter {metric!r}"
    when = day or utc_day()
    for _ in range(_RESERVE_TRIES):
        used = (await read_counters(session, tenant_id, when, [metric]))[metric]
        want = min(n, limit - used)
        if want <= 0:
            return 0
        stmt = insert(FairUseCounter).values(
            tenant_id=tenant_id, day=when, metric=metric, value=want
        )
        upsert = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "day", "metric"],
            set_={"value": FairUseCounter.value + stmt.excluded.value},
            where=FairUseCounter.value + stmt.excluded.value <= limit,
        ).returning(FairUseCounter.value)
        if (await session.execute(upsert)).first() is not None:
            return want
    return 0


async def release(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    metric: str,
    n: int,
    day: date | None = None,
) -> None:
    """Give back `n` units taken with `reserve` and not used (never below zero). The caller
    commits."""
    assert metric in COUNTER_METRICS, f"unknown fair-use counter {metric!r}"
    if n <= 0:
        return
    await session.execute(
        update(FairUseCounter)
        .where(
            FairUseCounter.tenant_id == tenant_id,
            FairUseCounter.day == (day or utc_day()),
            FairUseCounter.metric == metric,
        )
        .values(value=func.greatest(FairUseCounter.value - n, 0))
    )


async def read_counters(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    day: date | None = None,
    metrics: Iterable[str] = COUNTER_METRICS,
) -> dict[str, int]:
    """Today's (or `day`'s) value of each counter; a metric never counted reads 0."""
    wanted = list(metrics)
    rows = await session.execute(
        select(FairUseCounter.metric, FairUseCounter.value).where(
            FairUseCounter.tenant_id == tenant_id,
            FairUseCounter.day == (day or utc_day()),
            FairUseCounter.metric.in_(wanted),
        )
    )
    found = {str(m): int(v) for m, v in rows.all()}
    return {m: found.get(m, 0) for m in wanted}
