"""TV1: the same reply twice in a row makes the next turn the handoff card."""

from typing import Any

import pytest

import callobuzz_rag.conversation  # noqa: F401  (registers the real handoff provider)
from callobuzz_rag.conversation.envelope import TokenEvent, UiEvent
from callobuzz_rag.conversation.loop_guard import (
    fingerprint,
    loop_detected,
    note_reply,
    reset,
)
from callobuzz_rag.conversation.offers import registry
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.types import Route
from tests.unit.conversation_helpers import FakeLLM, fake_orch, make_director, make_turn

BUSINESS = {
    "label": "business_question",
    "confidence": 0.9,
    "language": "en",
    "topic": None,
    "frustrated": False,
    "needs_human": False,
}


@pytest.fixture(autouse=True)
def _io(director_io: Any) -> Any:
    return director_io


def test_fingerprint_ignores_case_and_spacing_only() -> None:
    a = fingerprint(Route.DECLINE, "Sorry, I can only help with Shree Labs.")
    assert a == fingerprint(Route.DECLINE, "sorry,  I can only help with shree labs.")
    assert a != fingerprint(Route.NO_ANSWER, "Sorry, I can only help with Shree Labs.")
    assert a != fingerprint(Route.DECLINE, "Sorry, I can only help with Shree Labs!")
    assert len(a) == 16


def test_two_identical_replies_make_the_next_turn_a_handoff() -> None:
    st = ConversationState()
    note_reply(st, Route.DECLINE, "Sorry, I can only help with Shree Labs.")
    assert not loop_detected(st)
    note_reply(st, Route.DECLINE, "Sorry, I can only help with Shree Labs.")
    assert loop_detected(st)
    reset(st)
    assert not loop_detected(st) and st.reply_fp is None and st.repeat_count == 0


def test_small_talk_never_counts_and_a_different_reply_resets() -> None:
    st = ConversationState()
    for _ in range(3):
        note_reply(st, Route.SMALLTALK, "Hi! How can I help?")
    assert not loop_detected(st)
    note_reply(st, Route.GROUNDED, "Home collection is Rs 150.")
    note_reply(st, Route.GROUNDED, "Home collection is Rs 150 within Pune.")
    assert not loop_detected(st)
    assert st.repeat_count == 1


def test_small_talk_between_two_identical_replies_does_not_break_the_run() -> None:
    st = ConversationState()
    note_reply(st, Route.NO_ANSWER, "I could not find that.")
    note_reply(st, Route.SMALLTALK, "Hello!")
    note_reply(st, Route.NO_ANSWER, "I could not find that.")
    assert loop_detected(st)


def test_the_state_survives_a_round_trip() -> None:
    st = ConversationState()
    note_reply(st, Route.DECLINE, "x")
    note_reply(st, Route.DECLINE, "x")
    back = ConversationState.from_dict(st.to_dict())
    assert loop_detected(back) and back.reply_fp == st.reply_fp


def _text(events: list[Any]) -> str:
    return "".join(e.content for e in events if isinstance(e, TokenEvent))


async def test_director_third_turn_is_the_card() -> None:
    """The same no-answer reply twice; the third turn (any question) is the handoff card, with
    no model call and no search, and the counters start again."""
    llm = FakeLLM(json_replies=[BUSINESS, BUSINESS])
    orch = fake_orch(dense=0.05)  # the gate refuses: no documented answer
    director = make_director(llm, orch, registry=registry)
    state = ConversationState()
    cfg: dict[str, Any] = {
        "handoff": {"triggers": {"offer_after_misses": 0}},
        "scope": {"fallback": {"actions": []}},
    }
    replies = []
    for text in ("price of the zeta scan?", "price of the zeta scan please?"):
        turn = make_turn(text, state=state, **cfg)
        events = [e async for e in director.run_stream(turn)]
        replies.append((events[-1].route, _text(events)))
    assert replies[0] == replies[1] and replies[0][0] is Route.NO_ANSWER
    assert state.repeat_count == 2 and loop_detected(state)

    calls_before = len(llm.calls)
    orch.run_retrieval_pipeline.reset_mock()
    third = make_turn("price of the zeta scan?", state=state, **cfg)
    events = [e async for e in director.run_stream(third)]
    done = events[-1]
    assert done.route is Route.HANDOFF
    card = next(e.block for e in events if isinstance(e, UiEvent))
    assert card.reason == "loop"
    assert len(llm.calls) == calls_before, "a model call for a loop turn"
    orch.run_retrieval_pipeline.assert_not_awaited()
    assert state.repeat_count == 0 and state.reply_fp is None


async def test_without_a_card_the_loop_turn_gives_the_contact_line() -> None:
    llm = FakeLLM(json_replies=[BUSINESS])
    director = make_director(llm, fake_orch(dense=0.05), registry=registry)
    state = ConversationState(reply_fp="abc", repeat_count=2)
    turn = make_turn("price of the zeta scan?", state=state, handoff={"enabled": False})
    events = [e async for e in director.run_stream(turn)]
    assert events[-1].route is Route.VERBATIM and "+919822012345" in _text(events)
    assert state.repeat_count == 0
