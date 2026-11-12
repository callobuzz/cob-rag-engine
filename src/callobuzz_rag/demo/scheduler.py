"""The nightly reset and the first seed (API process only).

Every 60 seconds `tick` asks two questions. At start-up with no good reset on record (or one
older than 25 hours) it runs one 120 seconds later, which is the first seed of a new demo server
and the catch-up after a missed night. And once the reset time of the day has passed it runs the
night's reset, claimed in Redis so several API processes reset once. It never raises out of the
task and never blocks start-up.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from callobuzz_rag.core.exceptions import RAGKernelError
from callobuzz_rag.demo import reset

logger = logging.getLogger(__name__)

TICK_S: Final = 60
START_DELAY_S: Final = 120
SLOT_TTL_S: Final = 172_800
RETRY_AFTER_S: Final = 600
MAX_RETRIES: Final = 3

Run = Callable[[str], Awaitable[Any]]


def latest_slot(hhmm: str, now: datetime) -> datetime:
    """The most recent reset time at or before `now`."""
    hour, minute = (int(part) for part in hhmm.split(":"))
    today = now.astimezone(UTC).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return today if today <= now else today - timedelta(days=1)


class Scheduler:
    def __init__(
        self,
        redis: Any,
        run: Run,
        *,
        reset_at: str,
        started_at: datetime,
        start_delay_s: float = START_DELAY_S,
    ) -> None:
        self._redis = redis
        self._run = run
        self._reset_at = reset_at
        self._delay = timedelta(seconds=start_delay_s)
        # A reset time that had already passed when the server started is not "missed": the
        # first-seed check below covers a server that has no good reset.
        self._slot_done = latest_slot(reset_at, started_at)
        self._start_checked = False
        self._start_due: datetime | None = None
        # A reset that ended `failed` leaves the demo empty or half built: try again soon, at
        # most MAX_RETRIES times per slot (the counter restarts with the next slot).
        self._retry_due: datetime | None = None
        self._retries = 0

    async def tick(self, now: datetime) -> None:
        """One pass. Never raises."""
        try:
            await self._tick(now)
        except Exception:
            logger.warning("Demo scheduler pass failed", exc_info=True)

    async def _tick(self, now: datetime) -> None:
        if not self._start_checked:
            last = await self._last_reset()
            self._start_checked = True
            if reset.is_stale(last, now):
                self._start_due = now + self._delay
                logger.info("demo: no recent reset, seeding in %s s", int(self._delay.seconds))
        if self._start_due is not None and now >= self._start_due:
            self._start_due = None
            # another process may have seeded meanwhile
            if reset.is_stale(await self._last_reset(), now):
                await self._launch("start", now)
        if self._retry_due is not None and now >= self._retry_due:
            self._retry_due = None
            # no staleness check: the last good reset may be recent, the demo is what is broken
            await self._launch("retry", now)
        slot = latest_slot(self._reset_at, now)
        if slot > self._slot_done:
            self._slot_done = slot
            self._retries = 0
            claimed = await self._redis.set(
                f"demo:schedule:{slot.isoformat()}", "1", ex=SLOT_TTL_S, nx=True
            )
            if claimed:
                await self._launch("schedule", now)

    async def _last_reset(self) -> str | None:
        value = await self._redis.get(reset.LAST_RESET_KEY)
        if value is None:
            return None
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    async def _launch(self, trigger: str, now: datetime) -> None:
        try:
            report = await self._run(trigger)
            if getattr(report, "result", None) == "failed":
                self._plan_retry(trigger, now)
        except reset.ResetRunning:
            logger.info("demo: a reset is already running; %s reset skipped", trigger)
        except reset.ResetUnavailable:
            logger.warning("demo: %s reset not run, the queue server did not answer", trigger)
        except RAGKernelError:
            logger.warning("demo: %s reset failed", trigger, exc_info=True)

    def _plan_retry(self, trigger: str, now: datetime) -> None:
        if self._retries >= MAX_RETRIES:
            logger.warning("demo: %s reset failed; giving up until the next slot", trigger)
            return
        self._retries += 1
        self._retry_due = now + timedelta(seconds=RETRY_AFTER_S)
        logger.warning(
            "demo: %s reset failed; retry %s of %s in %s s",
            trigger,
            self._retries,
            MAX_RETRIES,
            RETRY_AFTER_S,
        )

    async def run_forever(self) -> None:
        while True:
            await self.tick(datetime.now(UTC))
            await asyncio.sleep(TICK_S)


def start_scheduler(app: Any) -> "asyncio.Task[None]":
    """Start the task and keep it on `app.state` (a bare task may be garbage collected)."""
    from callobuzz_rag.demo import runtime

    scheduler = Scheduler(
        runtime.get_redis(app),
        lambda trigger: runtime.run_for_app(app, trigger),
        reset_at=app.state.settings.demo_reset_at_utc,
        started_at=datetime.now(UTC),
    )
    task = asyncio.create_task(scheduler.run_forever())
    app.state.demo_task = task
    return task


async def stop_scheduler(app: Any) -> None:
    task = getattr(app.state, "demo_task", None)
    if task is None:
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    app.state.demo_task = None
