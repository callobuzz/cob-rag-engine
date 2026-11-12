"""The director while answers are paused by a fair-use limit (PIP3 task 4): the same polite
reply as a balance pause (the business's own pause message and the handoff card), nothing
reaches a model, and only key and session callers are told why."""

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from callobuzz_rag.conversation.envelope import (
    DoneEvent,
    SourcesEvent,
    Surface,
    TokenEvent,
    UiEvent,
    to_sse,
)
from callobuzz_rag.conversation.schema import DEFAULT_PAUSED
from callobuzz_rag.conversation.types import Route
from tests.unit.conversation_helpers import FakeLLM, fake_orch, make_director, make_turn
from tests.unit.test_director_billing_paused import _registry


@pytest.fixture(autouse=True)
def _io(director_io: Any) -> Any:
    return director_io


async def _run(text: str, llm: FakeLLM):  # type: ignore[no-untyped-def]
    turn = make_turn(text)
    turn.paused_by = "fair_use"
    orch = fake_orch()
    orch.run_retrieval_pipeline = AsyncMock(side_effect=AssertionError("searched while paused"))
    events = [e async for e in make_director(llm, orch, _registry()).run_stream(turn)]
    return events, "".join(e.content for e in events if isinstance(e, TokenEvent))


async def test_a_greeting_is_still_answered_for_free() -> None:
    llm = FakeLLM()
    events, text = await _run("hello", llm)
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.route == Route.SMALLTALK and text
    assert llm.calls == []


async def test_a_question_gets_the_pause_message_and_the_card_and_costs_nothing() -> None:
    llm = FakeLLM()
    events, text = await _run("What are your timings?", llm)
    done = events[-1]
    assert text.startswith(DEFAULT_PAUSED["en"].rstrip(":"))
    assert [type(e) for e in events if isinstance(e, UiEvent)] == [UiEvent]
    assert done.route == Route.HANDOFF and done.paused is True
    assert done.pause_reason == "fair_use"
    assert llm.calls == []
    assert not [e for e in events if isinstance(e, SourcesEvent)]


async def test_a_balance_pause_says_billing() -> None:
    turn = make_turn("What are your timings?")
    turn.billing_paused = True
    orch = fake_orch()
    orch.run_retrieval_pipeline = AsyncMock(side_effect=AssertionError("searched while paused"))
    events = [e async for e in make_director(FakeLLM(), orch, _registry()).run_stream(turn)]
    assert events[-1].pause_reason == "billing"


def _done_body(done: DoneEvent, surface: Surface) -> dict[str, Any]:
    frame = to_sse(done, surface)
    assert frame is not None
    data = next(line for line in frame.splitlines() if line.startswith("data:"))
    return json.loads(data.removeprefix("data:").strip())


async def test_callers_with_a_key_or_session_are_told_why_but_the_widget_never_is() -> None:
    events, _ = await _run("What are your timings?", FakeLLM())
    done = events[-1]
    assert isinstance(done, DoneEvent)
    stream = _done_body(done, Surface.STREAM)
    conversation = _done_body(done, Surface.CONVERSATION)
    widget = _done_body(done, Surface.WIDGET)
    assert stream["paused"] is True and stream["pause_reason"] == "fair_use"
    assert conversation["pause_reason"] == "fair_use"
    flat = json.dumps(widget).lower()
    assert "paused" not in flat and "pause_reason" not in flat and "fair" not in flat


async def test_a_local_model_that_is_not_ready_says_so_not_a_fair_use_limit() -> None:
    """FU-9: the pause reply is the same polite one, but the caller is told no limit was hit."""
    turn = make_turn("What are your timings?")
    turn.paused_by = "local_unavailable"
    orch = fake_orch()
    orch.run_retrieval_pipeline = AsyncMock(side_effect=AssertionError("searched while paused"))
    llm = FakeLLM()
    events = [e async for e in make_director(llm, orch, _registry()).run_stream(turn)]
    done = events[-1]
    assert done.paused is True and done.pause_reason == "local_unavailable"
    assert "".join(e.content for e in events if isinstance(e, TokenEvent))
    assert llm.calls == []
