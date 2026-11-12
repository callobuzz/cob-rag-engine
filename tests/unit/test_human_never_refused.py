"""The pinned TV1 property: a request for a person is answered with the way to reach one in
every state, with no model call and no search, and a business with nothing to offer says so."""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import callobuzz_rag.conversation  # noqa: F401  (registers the real handoff + lead providers)
from callobuzz_rag.conversation.director import TurnInput
from callobuzz_rag.conversation.envelope import ErrorEvent, TokenEvent, UiEvent
from callobuzz_rag.conversation.handoff.provider import NO_CHANNEL_TEXT
from callobuzz_rag.conversation.offers import registry
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.types import Route
from tests.unit.conversation_helpers import FakeLLM, fake_orch, make_director, make_turn


@pytest.fixture(autouse=True)
def _io(director_io: Any) -> Any:
    return director_io


STATES: dict[str, Callable[[TurnInput], None]] = {
    "normal": lambda t: None,
    "billing_paused": lambda t: setattr(t, "billing_paused", True),
    "fair_use_paused": lambda t: setattr(t, "paused_by", "fair_use"),
    "busy": lambda t: setattr(t, "paused_by", "busy"),
    "learning": lambda t: setattr(t, "learning", True),
    "stopped": lambda t: setattr(t, "state", ConversationState(off_topic_strikes=3, stopped=True)),
    "card_already_offered": lambda t: setattr(t, "state", ConversationState(handoff_offered=True)),
    "human_only": lambda t: setattr(t, "human_only", True),
}
ASKS = ["talk to a person", "kisi se baat karni hai", "किसी इंसान से बात करनी है", "agent"]
ALLOWED = {Route.HANDOFF, Route.VERBATIM}  # VERBATIM only as the C5 contact line

_CARD = {
    "phone": "+919822012345",
    "whatsapp": "+919822012345",
    "email": "help@shree-labs.in",
    "website": "https://shree-labs.in",
}
_EMPTY_CARD = {"phone": None, "whatsapp": None, "email": None, "website": None}
_OPEN = [{"day": d, "open": "07:00", "close": "20:00"} for d in ("mon", "tue", "wed")]
_CLOSED = [{"day": "wed", "open": "07:00", "close": "08:00"}]


def _card_hours(office: str) -> dict[str, Any]:
    hours = _OPEN if office == "open" else _CLOSED
    return {"identity": {"card": {**_CARD, "hours": hours}}}


def _text(events: list[Any]) -> str:
    return "".join(e.content for e in events if isinstance(e, TokenEvent))


async def _run(turn: TurnInput, llm: FakeLLM | None = None) -> tuple[Any, list[Any]]:
    llm = llm or FakeLLM()
    llm.complete = AsyncMock(side_effect=AssertionError("model called"))  # type: ignore[method-assign]
    llm.complete_json = AsyncMock(side_effect=AssertionError("model called"))  # type: ignore[method-assign]
    llm.complete_stream = MagicMock(side_effect=AssertionError("model called"))  # type: ignore[method-assign]
    orch = fake_orch()
    orch.run_retrieval_pipeline = AsyncMock(side_effect=AssertionError("searched"))
    director = make_director(llm, orch, registry=registry)
    events = [e async for e in director.run_stream(turn)]
    return events[-1], events


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("ask", ASKS)
@pytest.mark.parametrize("office", ["open", "closed"])
async def test_a_request_for_a_person_is_always_answered_with_the_way_to_reach_one(
    state: str, ask: str, office: str, _io: Any
) -> None:
    turn = make_turn(ask, **_card_hours(office))
    STATES[state](turn)
    done, events = await _run(turn)
    assert not any(isinstance(e, ErrorEvent) for e in events), events
    assert done.route in ALLOWED
    if done.route is Route.HANDOFF:
        card = next(e.block for e in events if isinstance(e, UiEvent)).model_copy()
        assert card.kind == "handoff_card" and card.channels, "a card with no way to reach anyone"
        if office == "closed":
            assert "opens again" in card.message.lower() or any(
                c.kind == "callback" for c in card.channels
            )
    else:
        assert "+919822012345" in _text(events)
    _io.meter.assert_not_awaited()  # a free turn: no usage row


async def test_handoff_disabled_still_gives_the_contact_line() -> None:
    turn = make_turn("talk to a person", handoff={"enabled": False})
    done, events = await _run(turn)
    assert done.route is Route.VERBATIM and "+91" in _text(events)


async def test_nothing_on_the_card_says_so_and_never_declines() -> None:
    turn = make_turn("talk to a person", identity={"card": _EMPTY_CARD}, leads={"enabled": False})
    done, events = await _run(turn)
    assert done.route in ALLOWED
    assert NO_CHANNEL_TEXT["en"].split("{")[0] in _text(events)


async def test_nothing_on_the_card_and_handoff_disabled_says_so_too() -> None:
    turn = make_turn(
        "talk to a person",
        identity={"card": _EMPTY_CARD},
        leads={"enabled": False},
        handoff={"enabled": False},
    )
    done, events = await _run(turn)
    assert done.route is Route.VERBATIM
    assert NO_CHANNEL_TEXT["en"].split("{")[0] in _text(events)


async def test_the_no_channel_text_names_the_business() -> None:
    turn = make_turn("talk to a person", identity={"card": _EMPTY_CARD}, leads={"enabled": False})
    _, events = await _run(turn)
    assert "Shree Labs" in _text(events) or any(
        isinstance(e, UiEvent) and "Shree Labs" in e.block.message for e in events
    )


async def test_an_api_surface_with_only_a_callback_says_the_no_channel_text_once() -> None:
    # TV1 review: callback needs the widget's form; on /query there is no card at all, so the
    # open message and the "can't connect you" text must not be stacked.
    from callobuzz_rag.conversation.types import Channel

    turn = make_turn("talk to a person", identity={"card": _EMPTY_CARD}, leads={"enabled": True})
    turn.channel = Channel.API
    done, events = await _run(turn)
    text = _text(events)
    assert done.route in ALLOWED
    assert NO_CHANNEL_TEXT["en"].split("{")[0] in text
    assert "You can reach our team here" not in text
    assert text.count(NO_CHANNEL_TEXT["en"].split("{")[0]) == 1


async def test_closed_with_only_a_phone_gives_the_phone_and_when_it_opens() -> None:
    # TV1 review: calls are an open-hours channel, leads are off, nothing else on the card. The
    # visitor must get the number and the next opening time, and no "leave a message" that has
    # nowhere to go.
    card = {**_EMPTY_CARD, "phone": "+919822012345", "hours": _CLOSED}
    turn = make_turn("talk to a person", identity={"card": card}, leads={"enabled": False})
    done, events = await _run(turn)
    assert done.route in ALLOWED
    text = _text(events) + " ".join(
        e.block.message for e in events if isinstance(e, UiEvent) and hasattr(e.block, "message")
    )
    assert "+919822012345" in text
    assert "open again" in text.lower()
    assert "leave a message" not in text.lower()
