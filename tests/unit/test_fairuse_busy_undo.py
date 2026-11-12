"""PIP-7: a question that got the busy reply was never answered, so it must not stay in the
per-minute and per-hour windows (a business whose visitors keep being told "one moment" would
otherwise be throttled for questions it never had answered)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from callobuzz_rag.fairuse import alerts, counters
from tests.unit.accounts_fakes import FakeRedis

TENANT = uuid.uuid4()
NOW = datetime(2026, 10, 5, 14, 20, 30, tzinfo=UTC)


async def test_an_undone_question_leaves_both_windows() -> None:
    redis = FakeRedis()
    for _ in range(3):
        await counters.hit_question(redis, TENANT, NOW)
    await counters.unhit_question(redis, TENANT, NOW)
    assert await counters.read_window(redis, TENANT, "minute", NOW) == 2
    assert await counters.read_window(redis, TENANT, "hour", NOW) == 2


async def test_undoing_never_goes_below_zero_or_touches_another_business() -> None:
    redis = FakeRedis()
    other = uuid.uuid4()
    await counters.hit_question(redis, other, NOW)
    await counters.unhit_question(redis, TENANT, NOW)  # nothing was counted for this one
    assert await counters.read_window(redis, TENANT, "minute", NOW) == 0
    assert await counters.read_window(redis, other, "minute", NOW) == 1


async def test_a_wait_that_crossed_the_minute_undoes_the_bucket_that_holds_the_hit() -> None:
    redis = FakeRedis()
    await counters.hit_question(redis, TENANT, NOW.replace(second=55))
    later = NOW.replace(second=5) + timedelta(minutes=1)  # the busy reply, 10 s later
    await counters.unhit_question(redis, TENANT, later)
    assert await counters.read_window(redis, TENANT, "hour", later) == 0
    key = counters.window_key(TENANT, "minute", int(NOW.replace(second=55).timestamp() // 60))
    assert int(redis.values[key]) == 0


async def test_a_dead_redis_never_fails_the_busy_reply() -> None:
    redis = FakeRedis()
    redis.down = True
    assert await counters.unhit_question(redis, TENANT, NOW) is False


async def test_the_busy_reply_gives_the_question_back_in_the_background() -> None:
    redis = FakeRedis()
    await counters.hit_question(redis, TENANT)
    factory = MagicMock()
    session = MagicMock()
    session.commit = AsyncMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    alerts.schedule_busy(factory, TENANT, redis=redis)
    await asyncio.gather(*alerts._busy_tasks)
    assert await counters.read_window(redis, TENANT, "hour") == 0


# ------------------------------------------------------------- the sites that give busy replies

import uuid as _uuid  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from callobuzz_rag.api.errors import APIError  # noqa: E402
from callobuzz_rag.api.routes.query import hold_slot_or_busy  # noqa: E402
from callobuzz_rag.conversation.envelope import DoneEvent  # noqa: E402
from callobuzz_rag.conversation.types import Route  # noqa: E402
from callobuzz_rag.core.config import Settings  # noqa: E402
from callobuzz_rag.tiers.lanes import AnswerLanes, LaneConfig  # noqa: E402
from callobuzz_rag.tiers.schema import EffectiveProfile, ProfileConfig  # noqa: E402
from callobuzz_rag.tiers.slot import SlotSpec  # noqa: E402
from tests.unit.conversation_helpers import (  # noqa: E402
    FakeLLM,
    fake_orch,
    make_director,
    make_turn,
)

CFG = LaneConfig(slots=1, reserve_high=0, low_max=0, lease_s=60, max_wait_s=0.2, poll_ms=(5, 10))


async def test_a_director_busy_reply_gives_the_question_back(director_io: object) -> None:
    redis = FakeRedis()
    lanes = AnswerLanes(redis, {"engine": CFG})
    blocker = await lanes.acquire(
        pool="engine", tenant_id=_uuid.uuid4(), priority="normal", max_concurrency=None
    )
    turn = make_turn("what are your prices?")
    await counters.hit_question(redis, turn.tenant_id)  # what the gate did before the wait
    turn.slot = SlotSpec(lanes=lanes, pool="engine", priority="normal", max_concurrency=None)
    director = make_director(FakeLLM(), fake_orch())
    events = [e async for e in director.run_stream(turn)]
    await asyncio.gather(*alerts._busy_tasks)
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.route == Route.HANDOFF
    assert await counters.read_window(redis, turn.tenant_id, "hour") == 0
    await blocker.release()


async def test_a_legacy_busy_answer_gives_the_question_back() -> None:
    redis = FakeRedis()
    lanes = AnswerLanes(redis, {"engine": CFG})
    tenant = _uuid.uuid4()
    blocker = await lanes.acquire(
        pool="engine", tenant_id=_uuid.uuid4(), priority="normal", max_concurrency=None
    )
    await counters.hit_question(redis, tenant)
    app = SimpleNamespace(
        state=SimpleNamespace(
            settings=Settings(_env_file=None, answer_slots=1),
            answer_lanes=lanes,
            fair_use_redis=redis,
            session_factory=None,
        )
    )
    profile = EffectiveProfile(None, "Standard", "Standard", ProfileConfig(), "plan", True)
    with pytest.raises(APIError) as busy:
        await hold_slot_or_busy(SimpleNamespace(app=app), profile, tenant)  # type: ignore[arg-type]
    assert busy.value.code == "answers_busy"
    await asyncio.gather(*alerts._busy_tasks)
    assert await counters.read_window(redis, tenant, "hour") == 0
    await blocker.release()
