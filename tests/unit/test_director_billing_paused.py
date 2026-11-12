"""The director while answers are paused for balance (BIL4 task 4): free routes still answer,
a question gets the pause message and the handoff card, and nothing reaches a model."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from callobuzz_rag.api.schemas.assistant import HandoffCardBlock
from callobuzz_rag.conversation.envelope import (
    DoneEvent,
    MetaEvent,
    SourcesEvent,
    Surface,
    TokenEvent,
    UiEvent,
    to_sse,
)
from callobuzz_rag.conversation.offers import OfferKind, OfferRegistry
from callobuzz_rag.conversation.schema import DEFAULT_PAUSED, HandoffMessages
from callobuzz_rag.conversation.types import Route
from tests.unit.conversation_helpers import FakeLLM, fake_orch, make_director, make_turn


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


async def _run(text: str, llm: FakeLLM, *, registry: OfferRegistry | None = None, **cfg: Any):  # type: ignore[no-untyped-def]
    turn = make_turn(text, **cfg)
    turn.billing_paused = True
    orch = fake_orch()
    orch.run_retrieval_pipeline = AsyncMock(side_effect=AssertionError("searched while paused"))
    events = [e async for e in make_director(llm, orch, registry or _registry()).run_stream(turn)]
    text_out = "".join(e.content for e in events if isinstance(e, TokenEvent))
    return events, text_out


async def test_greeting_is_still_answered_for_free(_io: Any) -> None:
    llm = FakeLLM()
    events, text = await _run("hello", llm)
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.route == Route.SMALLTALK and text
    assert llm.calls == []


async def test_question_gets_the_pause_message_and_the_handoff_card(_io: Any) -> None:
    llm = FakeLLM()
    events, text = await _run("what are your prices?", llm)
    done = events[-1]
    assert text.startswith(DEFAULT_PAUSED["en"].rstrip(":"))
    assert [type(e) for e in events if isinstance(e, UiEvent)] == [UiEvent]
    assert done.route == Route.HANDOFF and done.paused is True
    assert llm.calls == []
    assert not [e for e in events if isinstance(e, SourcesEvent)]
    meta = next(e for e in events if isinstance(e, MetaEvent))
    assert meta.route == Route.HANDOFF


async def test_custom_pause_message_and_language(_io: Any) -> None:
    messages = {"paused": {"en": "Back soon!", "hi": "जल्द लौटेंगे!"}}
    _, text = await _run("आपकी कीमत क्या है?", FakeLLM(), handoff={"messages": messages})
    assert text.startswith("जल्द लौटेंगे!")


async def test_handoff_disabled_falls_back_to_the_contact_line(_io: Any) -> None:
    events, text = await _run("what are your prices?", FakeLLM(), handoff={"enabled": False})
    assert text.startswith(DEFAULT_PAUSED["en"].rstrip(":"))
    assert "+919822012345" in text
    assert not [e for e in events if isinstance(e, UiEvent)]


async def test_no_registered_card_falls_back_to_the_contact_line(_io: Any) -> None:
    _, text = await _run("what are your prices?", FakeLLM(), registry=OfferRegistry())
    assert "+919822012345" in text


async def test_paused_turn_is_not_metered(_io: Any) -> None:
    await _run("what are your prices?", FakeLLM())
    _io.meter.assert_not_awaited()


async def test_not_paused_is_byte_identical_to_today(_io: Any) -> None:
    llm = FakeLLM()
    turn = make_turn("hello")
    assert turn.billing_paused is False
    events = [e async for e in make_director(llm).run_stream(turn)]
    assert events[-1].paused is False  # type: ignore[union-attr]
    sse = to_sse(events[-1], Surface.STREAM)
    assert sse is not None and '"paused"' not in sse


async def test_paused_done_reaches_callers_but_never_the_widget(_io: Any) -> None:
    events, _ = await _run("what are your prices?", FakeLLM())
    done = events[-1]
    assert '"paused": true' in (to_sse(done, Surface.STREAM) or "").replace(":true", ": true")
    assert "paused" not in (to_sse(done, Surface.WIDGET) or "")


def test_stored_configs_without_the_message_read_the_default() -> None:
    assert HandoffMessages.model_validate({}).paused == DEFAULT_PAUSED
