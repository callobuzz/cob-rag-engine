"""PIP1: the director behind the answer lanes. A queued turn says so once, a wait that ends in a
timeout becomes the polite busy reply (handoff card, no model call, nothing metered, never an
error), and a free turn (paused, learning) never touches the lanes."""

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from callobuzz_rag.api.schemas.assistant import HandoffCardBlock
from callobuzz_rag.conversation.envelope import (
    DoneEvent,
    ErrorEvent,
    SourcesEvent,
    StageEvent,
    Surface,
    TokenEvent,
    UiEvent,
    to_sse,
)
from callobuzz_rag.conversation.offers import OfferKind, OfferRegistry
from callobuzz_rag.conversation.schema import DEFAULT_BUSY, HandoffMessages
from callobuzz_rag.conversation.types import Route
from callobuzz_rag.tiers.lanes import AnswerLanes, LaneConfig, slot_key
from callobuzz_rag.tiers.slot import SlotSpec
from tests.unit.accounts_fakes import FakeRedis
from tests.unit.conversation_helpers import FakeLLM, fake_orch, make_director, make_turn

CFG = LaneConfig(slots=1, reserve_high=0, low_max=0, lease_s=60, max_wait_s=0.25, poll_ms=(5, 10))


@pytest.fixture(autouse=True)
def _io(director_io: Any) -> Any:
    return director_io


class Card:
    kind = OfferKind.HANDOFF_CARD

    def wanted(self, ctx: Any) -> str | None:
        return None

    async def build(self, ctx: Any, reason: str) -> HandoffCardBlock:
        return HandoffCardBlock(open_now=True, message="Talk to us", channels=[], reason=reason)


def _registry() -> OfferRegistry:
    reg = OfferRegistry()
    reg.register(Card())
    return reg


class World:
    def __init__(self) -> None:
        self.redis = FakeRedis()
        self.lanes = AnswerLanes(self.redis, {"engine": CFG})
        self.spec = SlotSpec(
            lanes=self.lanes, pool="engine", priority="normal", max_concurrency=None
        )

    async def hold(self) -> Any:
        return await self.lanes.acquire(
            pool="engine", tenant_id=uuid.uuid4(), priority="normal", max_concurrency=None
        )

    async def free(self) -> bool:
        return await self.redis.get(slot_key("engine", 0)) is None


@pytest.fixture
def world() -> World:
    return World()


async def _run(turn: Any, llm: FakeLLM, orch: Any = None) -> list[Any]:
    director = make_director(llm, orch or fake_orch(), _registry())
    return [e async for e in director.run_stream(turn)]


def _text(events: list[Any]) -> str:
    return "".join(e.content for e in events if isinstance(e, TokenEvent))


async def test_a_timeout_becomes_the_busy_reply_with_the_handoff_card(
    world: World, _io: Any
) -> None:
    blocker = await world.hold()
    llm = FakeLLM()
    orch = fake_orch()
    orch.run_retrieval_pipeline = AsyncMock(side_effect=AssertionError("searched while busy"))
    turn = make_turn("what are your prices?")
    turn.slot = world.spec
    events = await _run(turn, llm, orch)
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.route == Route.HANDOFF
    assert not [e for e in events if isinstance(e, ErrorEvent)]  # never an error
    assert _text(events).startswith(DEFAULT_BUSY["en"].rstrip(":"))
    assert [type(e) for e in events if isinstance(e, UiEvent)] == [UiEvent]  # the card
    assert llm.calls == []  # no model call
    assert not [e for e in events if isinstance(e, SourcesEvent)]
    _io.meter.assert_not_awaited()  # not metered
    assert done.paused is False  # a busy reply is not a pause: no billing banner anywhere
    await blocker.release()


async def test_the_wait_is_announced_once_then_the_answer_flows(world: World) -> None:
    blocker = await world.hold()
    llm = FakeLLM(json_replies=[], stream=["Home collection costs Rs 150 [1]."])
    turn = make_turn("how much is home collection?")
    turn.slot = world.spec

    async def free_soon() -> None:
        await asyncio.sleep(0.08)
        await blocker.release()

    freeing = asyncio.create_task(free_soon())
    events = await _run(turn, llm)
    await freeing
    stages = [e.stage for e in events if isinstance(e, StageEvent)]
    assert stages.count("queued") == 1
    assert stages.index("queued") == 1  # right after the first frame, before any search
    assert stages[0] == "understand"
    assert isinstance(events[-1], DoneEvent) and events[-1].route != Route.HANDOFF
    assert await world.free()  # the slot was released at the end


async def test_no_queued_stage_when_a_slot_is_free(world: World) -> None:
    turn = make_turn("hello")
    turn.slot = world.spec
    events = await _run(turn, FakeLLM())
    assert "queued" not in [e.stage for e in events if isinstance(e, StageEvent)]
    assert await world.free()


async def test_the_slot_is_released_when_the_client_leaves_mid_stream(world: World) -> None:
    llm = FakeLLM(stream=["a", "b", "c"], delay_s=0.01)
    turn = make_turn("how much is home collection?")
    turn.slot = world.spec
    director = make_director(llm, fake_orch(), _registry())
    stream = director.run_stream(turn)
    seen = 0
    async for event in stream:
        seen += 1
        if isinstance(event, TokenEvent):
            break
    assert seen
    await stream.aclose()
    assert await world.free()


async def test_the_slot_is_released_when_the_turn_fails(world: World) -> None:
    orch = fake_orch()
    orch.run_retrieval_pipeline = AsyncMock(side_effect=RuntimeError("search is down"))
    turn = make_turn("how much is home collection?")
    turn.slot = world.spec
    events = await _run(turn, FakeLLM(), orch)
    assert any(isinstance(e, ErrorEvent) for e in events)
    assert await world.free()


class ExplodingLanes:
    """Any use of the lanes fails the test."""

    def config(self, pool: str) -> Any:
        raise AssertionError("a free turn must not touch the lanes")

    async def acquire(self, **kwargs: Any) -> Any:
        raise AssertionError("a free turn must not acquire a slot")


@pytest.mark.parametrize("how", ["billing_paused", "learning", "paused_by_busy"])
async def test_a_free_turn_never_acquires(how: str) -> None:
    turn = make_turn("what are your prices?")
    turn.slot = SlotSpec(
        lanes=ExplodingLanes(),  # type: ignore[arg-type]
        pool="engine",
        priority="normal",
        max_concurrency=None,
    )
    if how == "billing_paused":
        turn.billing_paused = True
    elif how == "learning":
        turn.learning = True
    else:
        turn.paused_by = "busy"
    events = await _run(turn, FakeLLM())
    assert isinstance(events[-1], DoneEvent)


async def test_a_greeting_never_queues_for_a_slot(world: World) -> None:
    # A turn the rules answer alone is free: it neither waits for a slot nor takes one.
    blocker = await world.hold()
    turn = make_turn("hello")
    turn.slot = SlotSpec(
        lanes=ExplodingLanes(),  # type: ignore[arg-type]
        pool="engine",
        priority="normal",
        max_concurrency=None,
    )
    events = await _run(turn, FakeLLM())
    assert isinstance(events[-1], DoneEvent) and _text(events)
    assert not _text(events).startswith(DEFAULT_BUSY["en"].rstrip(":"))
    assert "queued" not in [e.stage for e in events if isinstance(e, StageEvent)]
    await blocker.release()


async def test_a_patient_default_slot_never_produces_the_busy_reply(world: World) -> None:
    blocker = await world.hold()
    llm = FakeLLM(json_replies=[], stream=["Home collection costs Rs 150 [1]."])
    director = make_director(llm, fake_orch(), _registry())
    director.default_slot = SlotSpec(
        lanes=world.lanes,
        pool="engine",
        priority="normal",
        max_concurrency=None,
        patient=True,
    )

    async def free_later() -> None:
        await asyncio.sleep(0.6)  # longer than two waits of CFG.max_wait_s
        await blocker.release()

    freeing = asyncio.create_task(free_later())
    events = [e async for e in director.run_stream(make_turn("how much is home collection?"))]
    await freeing
    assert not _text(events).startswith(DEFAULT_BUSY["en"].rstrip(":"))
    assert isinstance(events[-1], DoneEvent) and events[-1].route != Route.HANDOFF
    assert await world.free()


async def test_default_slot_applies_to_a_turn_without_its_own(world: World) -> None:
    blocker = await world.hold()
    director = make_director(FakeLLM(), fake_orch(), _registry())
    director.default_slot = world.spec
    events = [e async for e in director.run_stream(make_turn("what are your prices?"))]
    assert _text(events).startswith(DEFAULT_BUSY["en"].rstrip(":"))
    await blocker.release()


async def test_busy_message_is_custom_and_localised(world: World) -> None:
    blocker = await world.hold()
    messages = {"busy": {"en": "Too many chats, try later!", "hi": "बहुत भीड़ है, बाद में आइए!"}}
    turn = make_turn("आपकी कीमत क्या है?", handoff={"messages": messages})
    turn.slot = world.spec
    events = await _run(turn, FakeLLM())
    assert _text(events).startswith("बहुत भीड़ है")
    await blocker.release()


def test_stored_configs_without_the_message_read_the_default() -> None:
    assert HandoffMessages.model_validate({}).busy == DEFAULT_BUSY
    assert set(DEFAULT_BUSY) == {"en", "hi", "hi-Latn"}
    assert all(len(text) <= 300 for text in DEFAULT_BUSY.values())


async def test_the_widget_done_never_says_paused_or_busy(world: World) -> None:
    blocker = await world.hold()
    turn = make_turn("what are your prices?")
    turn.slot = world.spec
    events = await _run(turn, FakeLLM())
    sse = to_sse(events[-1], Surface.WIDGET) or ""
    assert "paused" not in sse and "busy" not in sse
    await blocker.release()
