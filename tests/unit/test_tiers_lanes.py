"""PIP1: priority lanes (Redis leases) on a fake Redis."""

import asyncio
import logging
import uuid
from dataclasses import replace

import pytest

from callobuzz_rag.tiers.lanes import (
    AnswerLanes,
    LaneConfig,
    LaneTimeout,
    allowed_slots,
    lane_configs,
    slot_key,
    tenant_key,
)
from tests.unit.accounts_fakes import FakeRedis

CFG = LaneConfig(slots=4, reserve_high=1, low_max=1, lease_s=60, max_wait_s=0.4, poll_ms=(5, 10))


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


def lanes_for(redis: FakeRedis, cfg: LaneConfig = CFG) -> AnswerLanes:
    return AnswerLanes(redis, {"engine": cfg, "local": replace(cfg, slots=1, reserve_high=0)})


async def take(lanes: AnswerLanes, priority: str = "normal", tenant: uuid.UUID | None = None, **kw):
    return await lanes.acquire(
        pool="engine",
        tenant_id=tenant or uuid.uuid4(),
        priority=priority,  # type: ignore[arg-type]
        max_concurrency=kw.pop("max_concurrency", None),
        **kw,
    )


def test_allowed_slots() -> None:
    assert list(allowed_slots("high", CFG)) == [0, 1, 2, 3]
    assert list(allowed_slots("normal", CFG)) == [0, 1, 2]
    assert list(allowed_slots("low", CFG)) == [0]
    assert list(allowed_slots("low", replace(CFG, low_max=0))) == [0, 1]  # 0 = half
    assert list(allowed_slots("high", replace(CFG, slots=0))) == []


def test_a_small_pool_still_serves_normal_and_low() -> None:
    one = replace(CFG, slots=1, reserve_high=1, low_max=0)
    assert [list(allowed_slots(p, one)) for p in ("high", "normal", "low")] == [[0], [0], [0]]
    two = replace(CFG, slots=2, reserve_high=5, low_max=0)
    assert list(allowed_slots("normal", two)) == [0]  # the reserve never takes the last open slot


async def test_low_never_takes_the_high_reserve(fake_redis: FakeRedis) -> None:
    lanes = lanes_for(fake_redis)
    held = [await take(lanes, "normal") for _ in range(3)]
    with pytest.raises(LaneTimeout):
        await take(lanes, "low")
    high = await take(lanes, "high")
    assert high.slot == 3  # the reserve
    assert [h.slot for h in held] == [0, 1, 2]


async def test_high_is_never_starved_by_a_low_flood(fake_redis: FakeRedis) -> None:
    lanes = lanes_for(fake_redis)
    lows = [asyncio.create_task(take(lanes, "low")) for _ in range(20)]
    await asyncio.sleep(0)  # let the flood start
    high = await take(lanes, "high")
    assert high.slot is not None and high.waited_ms < 50
    results = await asyncio.gather(*lows, return_exceptions=True)
    got = [r for r in results if not isinstance(r, BaseException)]
    assert len(got) <= 1  # low may hold one slot at most
    assert all(isinstance(r, LaneTimeout) for r in results if isinstance(r, BaseException))


async def test_per_business_concurrency_works_with_the_pool_off(fake_redis: FakeRedis) -> None:
    t = uuid.uuid4()
    lanes = lanes_for(fake_redis, replace(CFG, slots=0))
    a = await take(lanes, tenant=t, max_concurrency=1)
    assert (a.slot, a.tenant_slot) == (None, 0)
    with pytest.raises(LaneTimeout):
        await take(lanes, tenant=t, max_concurrency=1)
    other = await take(lanes, tenant=uuid.uuid4(), max_concurrency=1)  # another business is free
    assert other.tenant_slot == 0
    await a.release()
    again = await take(lanes, tenant=t, max_concurrency=1)
    assert again.tenant_slot == 0


async def test_a_waiting_business_gives_its_slot_back_on_timeout(fake_redis: FakeRedis) -> None:
    t = uuid.uuid4()
    lanes = lanes_for(fake_redis, replace(CFG, slots=1, reserve_high=0))
    blocker = await take(lanes, "normal")  # takes the only pool slot
    with pytest.raises(LaneTimeout):
        await take(lanes, "normal", tenant=t, max_concurrency=2)
    assert await fake_redis.get(tenant_key(t, 0)) is None  # the business slot was released
    await blocker.release()


async def test_release_never_frees_someone_elses_slot(fake_redis: FakeRedis) -> None:
    lanes = lanes_for(fake_redis, replace(CFG, slots=1, reserve_high=0))
    a = await take(lanes)
    fake_redis.advance(61)  # the lease expired
    b = await take(lanes)
    assert b.slot == a.slot == 0
    await a.release()  # a's token no longer owns the key
    assert await fake_redis.get(slot_key("engine", 0)) is not None
    await b.release()
    assert await fake_redis.get(slot_key("engine", 0)) is None


async def test_release_is_idempotent_and_frees_both_slots(fake_redis: FakeRedis) -> None:
    t = uuid.uuid4()
    lanes = lanes_for(fake_redis)
    lease = await take(lanes, tenant=t, max_concurrency=1)
    assert lease.slot == 0 and lease.tenant_slot == 0
    await lease.release()
    await lease.release()
    assert await fake_redis.get(slot_key("engine", 0)) is None
    assert await fake_redis.get(tenant_key(t, 0)) is None


async def test_redis_down_fails_open(
    fake_redis: FakeRedis, caplog: pytest.LogCaptureFixture
) -> None:
    fake_redis.down = True
    with caplog.at_level(logging.WARNING):
        lease = await take(lanes_for(fake_redis))
    assert lease.slot is None and lease.tenant_slot is None
    assert "Answer lanes unavailable" in caplog.text
    await lease.release()  # a no-op, never raises


async def test_on_wait_is_called_with_a_growing_wait(fake_redis: FakeRedis) -> None:
    lanes = lanes_for(fake_redis, replace(CFG, slots=1, reserve_high=0, max_wait_s=0.3))
    blocker = await take(lanes)
    waits: list[int] = []

    async def on_wait(ms: int) -> None:
        waits.append(ms)

    with pytest.raises(LaneTimeout):
        await take(lanes, on_wait=on_wait)
    assert len(waits) >= 2 and waits == sorted(waits) and waits[-1] > waits[0]
    await blocker.release()


async def test_a_queued_question_gets_the_slot_when_it_frees(fake_redis: FakeRedis) -> None:
    lanes = lanes_for(fake_redis, replace(CFG, slots=1, reserve_high=0, max_wait_s=2))
    first = await take(lanes)
    waiting = asyncio.create_task(take(lanes))
    await asyncio.sleep(0.05)
    assert not waiting.done()
    await first.release()
    second = await waiting
    assert second.slot == 0 and second.waited_ms >= 40


async def test_cancelling_a_waiting_question_releases_what_it_took(fake_redis: FakeRedis) -> None:
    t = uuid.uuid4()
    lanes = lanes_for(fake_redis, replace(CFG, slots=1, reserve_high=0, max_wait_s=5))
    blocker = await take(lanes)
    waiting = asyncio.create_task(take(lanes, tenant=t, max_concurrency=2))
    await asyncio.sleep(0.05)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert await fake_redis.get(tenant_key(t, 0)) is None
    await blocker.release()


async def test_refresh_extends_the_lease(fake_redis: FakeRedis) -> None:
    lanes = lanes_for(fake_redis, replace(CFG, slots=1, reserve_high=0, lease_s=60))
    lease = await take(lanes)
    fake_redis.advance(50)
    await lease.refresh()
    fake_redis.advance(50)  # past the original expiry, inside the refreshed one
    assert await fake_redis.get(slot_key("engine", 0)) is not None
    fake_redis.advance(20)
    assert await fake_redis.get(slot_key("engine", 0)) is None


async def test_snapshot_counts_busy_slots_by_priority(fake_redis: FakeRedis) -> None:
    lanes = lanes_for(fake_redis)
    await take(lanes, "high")
    await take(lanes, "normal")
    await take(lanes, "normal")
    snap = await lanes.snapshot()
    assert snap["engine"] == {
        "slots": 4,
        "busy": 3,
        "by_priority": {"high": 1, "normal": 2, "low": 0},
    }
    assert snap["local"]["busy"] == 0


async def test_the_timeout_wait_can_be_overridden(fake_redis: FakeRedis) -> None:
    lanes = lanes_for(fake_redis, replace(CFG, slots=1, reserve_high=0, max_wait_s=0.05))
    first = await take(lanes)
    waiting = asyncio.create_task(take(lanes, max_wait_s=2))
    await asyncio.sleep(0.2)  # longer than the pool's own wait
    assert not waiting.done()
    await first.release()
    assert (await waiting).slot == 0


def test_lane_configs_read_the_settings() -> None:
    from callobuzz_rag.core.config import Settings

    s = Settings(
        _env_file=None,
        answer_slots=6,
        answer_slots_reserved_high=2,
        answer_slots_low_max=3,
        answer_queue_max_wait_seconds=15,
        answer_lease_seconds=120,
    )
    cfg = lane_configs(s)
    assert cfg["engine"] == LaneConfig(
        slots=6, reserve_high=2, low_max=3, lease_s=120, max_wait_s=15.0
    )
    assert cfg["local"].slots == 1  # one slot until the local model settings exist
    assert lane_configs(Settings(_env_file=None))["engine"].slots == 0  # off by default
