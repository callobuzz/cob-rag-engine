"""The daily website budgets of a business: fetches and new pages (PIP3).

`FetchBudget` is what a `WebsiteFetcher` spends from. Learning, freshness checks and pushed
URLs all read through the same fetcher, so they spend the same `fetches_per_day` budget. It sits
INSIDE the guarded HTTP client (an httpx request hook, run after the address and robots checks
and right before the bytes go out), so a URL that is refused is neither fetched nor counted and
the budget never opens a second, unguarded client. Counting is batched: the day's counter is
written every 20 fetches and when the fetcher closes, so a crawl is not slowed by a write per
page; with a limit the fetches are taken from the shared counter in blocks instead (see
`FetchBudget`). Past the limit the next fetch raises `FetchBudgetExhausted` and the run ends gracefully
(`counts.limited_by = "fetch_limit"`); the resume job picks the site up on the next UTC day.

`pages_left` is the same idea for new pages: how many more pages a business may learn today,
and `reserve_pages` / `release_pages` take them from the shared counter atomically.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from callobuzz_rag.core.exceptions import RAGKernelError
from callobuzz_rag.fairuse import counters
from callobuzz_rag.fairuse.limits import effective_fair_use

logger = logging.getLogger(__name__)

FLUSH_EVERY = 20


class FetchBudgetExhausted(RAGKernelError):  # noqa: N818 - a condition, named for what happened
    """The business has used its website fetches for today."""


def _today() -> date:
    return datetime.now(UTC).date()


@dataclass(slots=True)
class _State:
    day: date
    limit: int | None
    spent: int = 0  # fetches this budget has let through
    pending: int = 0  # no limit: spent, not yet written
    held: int = 0  # a limit: taken from the day's counter, not yet spent
    dry: bool = False  # the day's counter had nothing left to give
    told: bool = False  # the owner and the deployer were told the day's fetches ran out


class FetchBudget:
    """Spend one fetch at a time; raises once the day's limit is used.

    The limit is read on the first spend (and again after midnight UTC). With a limit the day's
    counter is SHARED by every run of the business (two sites, a discovery plus freshness
    checks and pushes), so fetches are taken from it atomically in small blocks
    (`counters.reserve`, never past the limit) and what a run did not spend is given back when
    its fetcher closes: several runs together can never pass the limit. A business without a
    limit still has its fetches counted, in batches, so the deployer sees them."""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        tenant_id: uuid.UUID,
        *,
        flush_every: int = FLUSH_EVERY,
        today: Callable[[], date] = _today,
    ) -> None:
        self._factory = session_factory
        self._tenant_id = tenant_id
        self._flush_every = flush_every
        self._today = today
        self._state: _State | None = None

    async def _load(self, day: date) -> _State:
        async with self._factory() as session:
            effective = await effective_fair_use(session, self._tenant_id)
        limit = None if effective.exempt else effective.limits.fetches_per_day
        return _State(day=day, limit=limit)

    async def _ensure(self) -> _State:
        day = self._today()
        state = self._state
        if state is not None and state.day != day:
            await self.close()
            state = None
        if state is None:
            state = self._state = await self._load(day)
        return state

    async def is_limited(self) -> bool:
        """Whether the business has a fetch limit today (a run then reads new pages first)."""
        return (await self._ensure()).limit is not None

    async def _take(self, state: _State) -> int:
        """Take the next block of the day's fetches from the shared counter."""
        assert state.limit is not None
        block = max(1, min(self._flush_every, state.limit // 5))
        async with self._factory() as session:
            got = await counters.reserve(
                session, self._tenant_id, "fetches", block, state.limit, state.day
            )
            await session.commit()
        return got

    async def spend(self) -> None:
        state = await self._ensure()
        if state.limit is None:
            state.spent += 1
            state.pending += 1
            if state.pending >= self._flush_every:
                await self.flush()
            return
        if state.held <= 0:
            state.held = await self._take(state)
            if state.held <= 0:
                state.dry = True
                if not state.told:  # the limit was just reached: tell them, once
                    state.told = True
                    self._tell()
                raise FetchBudgetExhausted("The website fetches of today are used")
        state.held -= 1
        state.spent += 1

    async def flush(self) -> None:
        """No limit: write what has been spent since the last write. Never raises into a
        crawl."""
        state = self._state
        if state is None or state.limit is not None or state.pending <= 0:
            return
        pending, state.pending = state.pending, 0
        try:
            async with self._factory() as session:
                await counters.bump(session, self._tenant_id, "fetches", pending, state.day)
                await session.commit()
        except Exception:
            state.pending += pending
            logger.warning("Could not write the website fetch count", exc_info=True)

    async def close(self) -> None:
        """End of the run: write the unwritten count, or give back the fetches that were taken
        from the day's counter and not spent. Never raises into a crawl."""
        state = self._state
        if state is None:
            return
        if state.limit is None:
            await self.flush()
            return
        held, state.held = state.held, 0
        if state.spent > 0:
            self._tell()  # 80 % may have been passed: the claim rows make each level one notice
        if held <= 0:
            return
        try:
            async with self._factory() as session:
                await counters.release(session, self._tenant_id, "fetches", held, state.day)
                await session.commit()
        except Exception:
            state.held += held
            logger.warning("Could not give back the unspent website fetches", exc_info=True)

    def _tell(self) -> None:
        """Check the day's fetches against the 80 % and 100 % alert levels, in the background (the
        claim rows make each level one notice per day, however often this runs)."""
        from callobuzz_rag.fairuse import alerts

        alerts.schedule_usage(self._factory, self._tenant_id, ("fetches_per_day",))

    @property
    def exhausted(self) -> bool:
        state = self._state
        return state is not None and state.limit is not None and state.held <= 0 and state.dry


async def pages_left(session: Any, tenant_id: uuid.UUID) -> int | None:
    """New pages the business may still learn today (None = no daily limit)."""
    effective = await effective_fair_use(session, tenant_id)
    limit = None if effective.exempt else effective.limits.website_pages_per_day
    if limit is None:
        return None
    used = (
        await counters.read_counters(session, tenant_id, counters.utc_day(), ["pages_learned"])
    )["pages_learned"]
    return max(limit - used, 0)


async def reserve_pages(session_factory: Callable[[], Any], tenant_id: uuid.UUID, n: int) -> int:
    """Take up to `n` of today's new pages from the business's shared counter, atomically
    (several runs at once can never learn more than the limit together). The pages count as
    learned from this moment; a run gives back what it did not learn with `release_pages`.
    A business without a limit gets all `n`, counted."""
    if n <= 0:
        return 0
    async with session_factory() as session:
        effective = await effective_fair_use(session, tenant_id)
        limit = None if effective.exempt else effective.limits.website_pages_per_day
        if limit is None:
            await counters.bump(session, tenant_id, "pages_learned", n)
            granted = n
        else:
            granted = await counters.reserve(session, tenant_id, "pages_learned", n, limit)
        await session.commit()
    return granted


async def release_pages(session_factory: Callable[[], Any], tenant_id: uuid.UUID, n: int) -> None:
    """Give back pages taken with `reserve_pages` and not learned. Best effort: never fails a
    run."""
    if n <= 0:
        return
    try:
        async with session_factory() as session:
            await counters.release(session, tenant_id, "pages_learned", n)
            await session.commit()
    except Exception:
        logger.warning("Could not give back the unlearned pages of the day", exc_info=True)


async def add_pages(session_factory: Callable[[], Any], tenant_id: uuid.UUID, n: int) -> None:
    """Count `n` pages learned today. Best effort: never fails a run."""
    if n <= 0:
        return
    try:
        async with session_factory() as session:
            await counters.bump(session, tenant_id, "pages_learned", n)
            await session.commit()
    except Exception:
        logger.warning("Could not write the pages-learned count", exc_info=True)
