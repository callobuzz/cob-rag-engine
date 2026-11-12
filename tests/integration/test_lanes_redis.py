"""PIP1 answer lanes on a real Redis. Needs Docker (skipped with the integration suite when down).

Two processes' worth of `AnswerLanes` (two clients) never hold more than `slots` slots at once
over 200 random acquires, and the compare-and-delete release works against real Redis.
"""

import asyncio
import random
import uuid

import pytest
import redis.asyncio as aioredis

from callobuzz_rag.tiers.lanes import AnswerLanes, LaneConfig, LaneTimeout, slot_key
from tests.integration.test_accounts_flow import _redis_url

pytestmark = pytest.mark.integration

CFG = LaneConfig(slots=3, reserve_high=1, low_max=1, lease_s=30, max_wait_s=10, poll_ms=(5, 20))


async def test_never_more_than_the_slots_at_once(redis_container) -> None:  # type: ignore[no-untyped-def]
    url = _redis_url(redis_container)
    clients = [aioredis.from_url(url), aioredis.from_url(url)]  # type: ignore[no-untyped-call]
    await clients[0].flushdb()
    lanes = [AnswerLanes(c, {"engine": CFG}) for c in clients]
    rng = random.Random(7)
    inside = 0
    peak = 0
    timeouts = 0

    async def one(n: int) -> None:
        nonlocal inside, peak, timeouts
        priority = rng.choice(["high", "normal", "normal", "low"])
        try:
            lease = await lanes[n % 2].acquire(
                pool="engine", tenant_id=uuid.uuid4(), priority=priority, max_concurrency=None
            )
        except LaneTimeout:
            timeouts += 1
            return
        inside += 1
        peak = max(peak, inside)
        await asyncio.sleep(rng.uniform(0.001, 0.01))
        inside -= 1
        await lease.release()

    try:
        await asyncio.gather(*(one(n) for n in range(200)))
        assert peak <= CFG.slots
        assert timeouts == 0  # a 10 s wait is plenty for 200 short answers
        keys = [k async for k in clients[0].scan_iter("pip:s:engine:*")]
        assert keys == []  # every slot was released
    finally:
        for c in clients:
            await c.aclose()


async def test_compare_and_delete_release(redis_container) -> None:  # type: ignore[no-untyped-def]
    client = aioredis.from_url(_redis_url(redis_container))  # type: ignore[no-untyped-call]
    await client.flushdb()
    lanes = AnswerLanes(client, {"engine": LaneConfig(1, 0, 0, 30, 1.0, (5, 10))})
    try:
        a = await lanes.acquire(
            pool="engine", tenant_id=uuid.uuid4(), priority="normal", max_concurrency=None
        )
        await client.delete(slot_key("engine", 0))  # the lease "expired"
        b = await lanes.acquire(
            pool="engine", tenant_id=uuid.uuid4(), priority="normal", max_concurrency=None
        )
        await a.release()  # must not free b's slot
        assert await client.get(slot_key("engine", 0)) is not None
        await b.refresh()
        await b.release()
        assert await client.get(slot_key("engine", 0)) is None
    finally:
        await client.aclose()
