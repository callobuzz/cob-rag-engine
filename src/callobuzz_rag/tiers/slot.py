"""Hold an answer slot around the work of one answer (PIP1).

`slot_spec` decides whether a lane applies at all (no engine-wide pool and no per-business
limit means nothing to wait for, and no Redis is touched). `SlotHold` is for the director's
stream, which has to keep sending progress while it waits; `answer_slot` is the plain async
context manager the other surfaces use. A slot is released on success, on an exception and when
the client goes away mid-stream, and refreshed while it is held so a long local answer outlives
the lease.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from callobuzz_rag.tiers.lanes import (
    AnswerLanes,
    LaneTimeout,
    Lease,
    get_lanes,
    lane_configs,
)
from callobuzz_rag.tiers.schema import EffectiveProfile, Priority

logger = logging.getLogger(__name__)

# Golden runs must finish: they wait this many times longer than a visitor's question.
GOLDEN_WAIT_FACTOR = 10


@dataclass(frozen=True, slots=True)
class SlotSpec:
    """What one answer needs from the lanes."""

    lanes: AnswerLanes
    pool: Literal["engine", "local"]
    priority: Priority
    max_concurrency: int | None
    max_wait_s: float | None = None  # None: the pool's own wait
    patient: bool = False  # a golden run: a timeout waits again, never the busy reply


def effective_pool(profile: EffectiveProfile) -> Literal["engine", "local"]:
    """The pool a profile's answers draw from: `local` only when its model is really there and
    the answer goes to it (a business with its own AI key answers on that, never on the CPU)."""
    if profile.config.answer_route == "local" and profile.available and not profile.own_key:
        return "local"
    return "engine"


def slot_spec(app: Any, profile: EffectiveProfile, *, patient: bool = False) -> "SlotSpec | None":
    """The slot this profile's answer must hold, or None when no lane applies (the pool is off
    and the profile limits no one business). `patient` is for golden runs: a much longer wait."""
    pool = effective_pool(profile)
    cfg = lane_configs(app.state.settings)[pool]
    max_concurrency = profile.config.max_concurrency
    if cfg.slots <= 0 and not max_concurrency:
        return None
    return SlotSpec(
        lanes=get_lanes(app),
        pool=pool,
        priority=profile.config.priority,
        max_concurrency=max_concurrency,
        max_wait_s=cfg.max_wait_s * GOLDEN_WAIT_FACTOR if patient else None,
        patient=patient,
    )


async def _keep_alive(lease: Lease) -> None:
    interval = max(lease.lease_s / 3, 1.0)
    while True:
        await asyncio.sleep(interval)
        await lease.refresh()


class SlotHold:
    """Acquire a slot while the caller keeps yielding progress, then release it.

    ``async for waited_ms in hold.wait(): ...`` yields once per poll while the question is
    queued; afterwards `hold.busy` says the wait ended in a timeout (answer with the busy
    reply, hold nothing). `await hold.release()` is safe to call more than once.
    """

    def __init__(self, spec: SlotSpec | None, tenant_id: uuid.UUID) -> None:
        self._spec = spec
        self._tenant_id = tenant_id
        self._lease: Lease | None = None
        self._keeper: asyncio.Task[None] | None = None
        self.busy = False

    async def wait(self) -> AsyncIterator[int]:
        spec = self._spec
        if spec is None:
            return
        self.busy = False  # a new wait after a timed-out one starts clean
        waits: asyncio.Queue[int] = asyncio.Queue()

        async def on_wait(waited_ms: int) -> None:
            waits.put_nowait(waited_ms)

        task = asyncio.ensure_future(
            spec.lanes.acquire(
                pool=spec.pool,
                tenant_id=self._tenant_id,
                priority=spec.priority,
                max_concurrency=spec.max_concurrency,
                on_wait=on_wait,
                max_wait_s=spec.max_wait_s,
            )
        )
        getter: asyncio.Future[int] | None = None
        try:
            while not task.done():
                getter = asyncio.ensure_future(waits.get())
                await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
                if getter.done():
                    waited = getter.result()
                    getter = None
                    yield waited
                else:
                    getter.cancel()
                    getter = None
            try:
                self._lease = task.result()
            except LaneTimeout:
                self.busy = True
                return
            if self._lease.keys:
                self._keeper = asyncio.ensure_future(_keep_alive(self._lease))
        except BaseException:
            if getter is not None:
                getter.cancel()
            task.cancel()
            with contextlib.suppress(BaseException):
                lease = await task
                await lease.release()
            raise

    async def release(self) -> None:
        keeper, self._keeper = self._keeper, None
        if keeper is not None:
            keeper.cancel()
        lease, self._lease = self._lease, None
        if lease is not None:
            await asyncio.shield(lease.release())


@contextlib.asynccontextmanager
async def answer_slot(
    request: Any,
    profile: EffectiveProfile,
    tenant_id: uuid.UUID,
    *,
    on_wait: Callable[[int], Awaitable[None]] | None = None,
    patient: bool = False,
) -> AsyncIterator[Lease | None]:
    """Hold the slot for the duration of the block. Raises `LaneTimeout` when none became free.
    Yields None when no lane applies. Used around retrieval + generation, never around a
    paused, learning or busy turn."""
    spec = slot_spec(request.app, profile, patient=patient)
    if spec is None:
        yield None
        return
    lease = await spec.lanes.acquire(
        pool=spec.pool,
        tenant_id=tenant_id,
        priority=spec.priority,
        max_concurrency=spec.max_concurrency,
        on_wait=on_wait,
        max_wait_s=spec.max_wait_s,
    )
    keeper = asyncio.ensure_future(_keep_alive(lease)) if lease.keys else None
    try:
        yield lease
    finally:
        if keeper is not None:
            keeper.cancel()
        await asyncio.shield(lease.release())


def worker_slot(
    settings: Any, profile: EffectiveProfile, *, patient: bool = False
) -> "tuple[SlotSpec | None, Callable[[], Awaitable[None]] | None]":
    """`slot_spec` for code with no app (a worker running a golden set): its own Redis client,
    returned with the closer the caller must run when it is done."""
    pool = effective_pool(profile)
    configs = lane_configs(settings)
    max_concurrency = profile.config.max_concurrency
    if configs[pool].slots <= 0 and not max_concurrency:
        return None, None
    import redis.asyncio as aioredis

    client = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, socket_timeout=2, socket_connect_timeout=2
    )
    spec = SlotSpec(
        lanes=AnswerLanes(client, configs),
        pool=pool,
        priority=profile.config.priority,
        max_concurrency=max_concurrency,
        max_wait_s=configs[pool].max_wait_s * GOLDEN_WAIT_FACTOR if patient else None,
        patient=patient,
    )
    return spec, client.aclose
