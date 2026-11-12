from types import SimpleNamespace
from typing import Any

import pytest

from callobuzz_rag.conversation.leads.triggers import lead_trigger
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.types import Channel, OfficeState, Route, TurnLabel


def _ctx(
    *,
    leads: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    channel: Channel = Channel.WIDGET,
    **signals: object,
) -> Any:
    raw = AssistantConfig().model_dump()
    raw["leads"]["triggers"].update(leads or {})
    config = AssistantConfig.model_validate(raw)
    sig = SimpleNamespace(
        label=TurnLabel.BUSINESS_QUESTION,
        route=Route.GROUNDED,
        office=OfficeState.OPEN,
        page_url=None,
        topic_id=None,
        asked_for_contact=False,
        is_preview=False,
    )
    for k, v in signals.items():
        setattr(sig, k, v)
    st = SimpleNamespace(lead_id=None, lead_offers=0, lead_dismissed=False, message_count=1)
    for k, v in (state or {}).items():
        setattr(st, k, v)
    return SimpleNamespace(config=config, signals=sig, state=st, language="en", channel=channel)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"label": TurnLabel.BUYING_INTENT}, "buying_intent"),
        ({"route": Route.NO_ANSWER}, "no_answer"),
        ({"office": OfficeState.CLOSED}, "out_of_hours"),
        ({}, None),
    ],
)
def test_default_triggers(kwargs: dict[str, Any], expected: str | None) -> None:
    assert lead_trigger(_ctx(**kwargs)) == expected


def test_configured_triggers() -> None:
    topic = _ctx(leads={"topics": ["home_collection"]}, topic_id="home_collection")
    assert lead_trigger(topic) == "topic"
    page = _ctx(
        leads={"page_url_globs": ["https://shree-labs.in/book/**"]},
        page_url="https://shree-labs.in/book/lipid",
    )
    assert lead_trigger(page) == "page_url"
    after = _ctx(leads={"after_n_messages": 3}, state={"message_count": 3})
    assert lead_trigger(after) == "after_n_messages"
    human = _ctx(leads={"human_request": True}, label=TurnLabel.HUMAN_REQUEST)
    assert lead_trigger(human) == "human_request"


def test_once_per_session_unless_asked() -> None:
    assert lead_trigger(_ctx(label=TurnLabel.BUYING_INTENT, state={"lead_offers": 1})) is None
    assert lead_trigger(_ctx(label=TurnLabel.BUYING_INTENT, state={"lead_dismissed": True})) is None
    asked = _ctx(asked_for_contact=True, state={"lead_offers": 1, "lead_dismissed": True})
    assert lead_trigger(asked) == "asked"
    assert lead_trigger(_ctx(asked_for_contact=True, state={"lead_id": "x"})) is None


def test_only_the_widget_and_never_a_preview() -> None:
    for channel in (Channel.API, Channel.STREAM, Channel.CONVERSATION, Channel.PLAYGROUND):
        assert lead_trigger(_ctx(channel=channel, label=TurnLabel.BUYING_INTENT)) is None
        assert lead_trigger(_ctx(channel=channel, asked_for_contact=True)) is None
    assert lead_trigger(_ctx(label=TurnLabel.BUYING_INTENT, is_preview=True)) is None


def test_disabled() -> None:
    raw = AssistantConfig().model_dump()
    raw["leads"]["enabled"] = False
    ctx = _ctx(label=TurnLabel.BUYING_INTENT)
    ctx.config = AssistantConfig.model_validate(raw)
    assert lead_trigger(ctx) is None


def _explicit(cta: str, *, whatsapp: str | None = "+919812345678", **kw: Any) -> Any:
    ctx = _ctx(label=TurnLabel.BUYING_INTENT, state={"cta_declined": []}, **kw)
    ctx.config.conversation.cta.buying_intent_cta = cta
    ctx.config.identity.card.whatsapp = whatsapp
    ctx.question = "I want to book home collection"
    return ctx


def test_an_explicit_cta_takes_the_slot_before_the_lead_form() -> None:
    assert lead_trigger(_explicit("whatsapp")) is None


def test_the_default_policy_leaves_the_slot_to_the_lead_form() -> None:
    assert lead_trigger(_explicit("lead_form")) == "buying_intent"


def test_the_lead_form_takes_the_slot_when_the_explicit_cta_has_no_target() -> None:
    assert lead_trigger(_explicit("whatsapp", whatsapp=None)) == "buying_intent"


def test_the_lead_form_takes_the_slot_when_the_visitor_declined_that_cta() -> None:
    ctx = _explicit("whatsapp")
    ctx.state.cta_declined = ["whatsapp"]
    assert lead_trigger(ctx) == "buying_intent"


def test_no_cta_at_all_leaves_the_slot_to_the_lead_form() -> None:
    assert lead_trigger(_explicit("none")) == "buying_intent"
    ctx = _explicit("whatsapp")
    ctx.config.conversation.cta.max_per_answer = 0
    assert lead_trigger(ctx) == "buying_intent"


def test_a_visitor_who_asks_to_be_contacted_still_gets_the_form() -> None:
    assert lead_trigger(_explicit("whatsapp", asked_for_contact=True)) == "asked"


def test_an_explicit_cta_does_not_hold_back_other_triggers() -> None:
    ctx = _ctx(label=TurnLabel.BUSINESS_QUESTION, route=Route.NO_ANSWER, state={"cta_declined": []})
    ctx.config.conversation.cta.buying_intent_cta = "whatsapp"
    ctx.config.identity.card.whatsapp = "+919812345678"
    assert lead_trigger(ctx) == "no_answer"
