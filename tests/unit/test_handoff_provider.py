"""The handoff card as an offer (C5): wanted() reasons, the card, notify-on-offer for APIs."""

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch

from callobuzz_rag.api.schemas.assistant import HandoffCardBlock
from callobuzz_rag.conversation.handoff.provider import CALLBACK_WINDOWS, HandoffCardProvider
from callobuzz_rag.conversation.offers import OfferContext, OfferKind, registry
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.signals import TurnSignals
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.types import Channel, OfficeState, Route, TurnLabel

CFG = AssistantConfig.model_validate(
    {
        "identity": {
            "card": {
                "whatsapp": "+919812345678",
                "phone": "+919876543210",
                "hours": [{"day": "mon", "open": "09:00", "close": "18:00"}],
            }
        },
        "handoff": {
            "channels": [
                {"kind": "whatsapp"},
                {"kind": "call", "available": "open_hours"},
                {"kind": "callback", "available": "closed_hours"},
            ],
            "messages": {
                "open": {"en": "Our team is around. Pick a way to reach us."},
                "closed": {"en": "We're closed now. **WhatsApp** us and we'll reply."},
            },
        },
    }
)
NO_MISSES = CFG.model_copy(
    update={
        "handoff": CFG.handoff.model_copy(
            update={"triggers": CFG.handoff.triggers.model_copy(update={"offer_after_misses": 0})}
        )
    }
)
MON_9PM_IST = datetime(2026, 10, 5, 15, 30, tzinfo=UTC)  # 21:00 IST Monday


def _ctx(
    channel: Channel = Channel.WIDGET,
    office: OfficeState = OfficeState.OPEN,
    config: AssistantConfig = CFG,
    **sig: object,
) -> OfferContext:
    signals: dict[str, object] = {
        "label": TurnLabel.HUMAN_REQUEST,
        "office": office,
        "text": "Can I talk to someone?",
        "now": MON_9PM_IST,
        "page_url": "https://shree-labs.in/tests",
    }
    signals.update(sig)
    return OfferContext(
        config=config,
        signals=TurnSignals.for_test(**signals),
        state=ConversationState(),
        language="en",
        corpus_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        channel=channel,
        answer_text="",
        cited_sources=[{"file_name": "refund-policy.pdf"}],
    )


def test_registered_once() -> None:
    import importlib

    import callobuzz_rag.conversation  # noqa: F401  (imports conversation.handoff.provider)

    importlib.import_module("callobuzz_rag.conversation.handoff.provider")
    assert len(registry.providers(OfferKind.HANDOFF_CARD)) == 1


def test_wanted_returns_the_reason() -> None:
    p = HandoffCardProvider()
    assert p.wanted(_ctx()) == "explicit_request"
    assert p.wanted(_ctx(label=TurnLabel.BUSINESS_QUESTION)) is None


async def test_forced_build_maps_the_reason() -> None:
    p = HandoffCardProvider()
    ctx = _ctx(label=TurnLabel.BUSINESS_QUESTION, route=Route.NO_ANSWER, gate_passed=False)
    ctx = replace(ctx, config=NO_MISSES)  # TV1: a miss would offer a person by itself
    assert p.wanted(ctx) is None
    with patch("callobuzz_rag.conversation.handoff.provider._enqueue_offered"):
        forced = await p.build(ctx, "forced")
        passed = await p.build(_ctx(label=TurnLabel.BUSINESS_QUESTION), "no_answer")
        asked = await p.build(_ctx(), "forced")
    assert isinstance(forced, HandoffCardBlock) and forced.reason == "no_answer"
    assert passed is not None and passed.reason == "no_answer"
    assert asked is not None and asked.reason == "explicit_request"


async def test_open_card() -> None:
    p = HandoffCardProvider()
    with patch("callobuzz_rag.conversation.handoff.provider._enqueue_offered") as enqueue:
        ctx = _ctx()
        card = await p.build(ctx, "explicit_request")
    assert isinstance(card, HandoffCardBlock) and card.open_now is True
    assert [c.kind for c in card.channels] == ["whatsapp", "call"]
    assert card.message == "Our team is around. Pick a way to reach us."
    assert card.reason == "explicit_request" and card.callback_form is None
    enqueue.assert_not_called()  # the widget notifies on a tap
    assert ctx.state.handoff_offered is True and ctx.state.last_handoff_reason == "explicit_request"


async def test_closed_card_says_when_we_open_and_carries_the_callback_form() -> None:
    p = HandoffCardProvider()
    ctx = _ctx(office=OfficeState.CLOSED)
    card = await p.build(ctx, "explicit_request")
    assert card is not None and card.open_now is False
    assert [c.kind for c in card.channels] == ["whatsapp", "callback"]
    assert "**WhatsApp**" in card.message and card.message.endswith(
        "We open again on Monday at 9:00 am."
    )
    form = card.callback_form
    assert form is not None and form.callback is True
    ids = [f.id for f in form.fields]
    assert ids[-2:] == ["callback_date", "callback_window"]
    window = form.fields[-1]
    assert window.options == list(CALLBACK_WINDOWS) and window.required
    # the form can be submitted: its id is registered, and it did not use up the lead offer
    assert any(entry.startswith(form.form_id) for entry in ctx.state.lead_form_ids)
    assert ctx.state.lead_offers == 0 and ctx.state.lead_offer_reason == "callback"


async def test_callback_needs_a_form_the_surface_can_take() -> None:
    p = HandoffCardProvider()
    with patch("callobuzz_rag.conversation.handoff.provider._enqueue_offered"):
        api = await p.build(_ctx(Channel.API, OfficeState.CLOSED), "forced")
    assert api is not None and [c.kind for c in api.channels] == ["whatsapp"]
    assert api.callback_form is None
    already = _ctx(office=OfficeState.CLOSED)
    already.state.lead_id = str(uuid.uuid4())
    card = await p.build(already, "explicit_request")
    assert card is not None and [c.kind for c in card.channels] == ["whatsapp"]


async def test_no_channels_means_no_card_for_an_unasked_offer() -> None:
    p = HandoffCardProvider()
    empty = CFG.model_copy(update={"handoff": CFG.handoff.model_copy(update={"channels": []})})
    assert await p.build(_ctx(config=empty), "no_answer") is None


# TV1 human
async def test_a_request_for_a_person_with_no_channel_gets_the_contact_line_card() -> None:
    p = HandoffCardProvider()
    empty = CFG.model_copy(update={"handoff": CFG.handoff.model_copy(update={"channels": []})})
    ctx = _ctx(config=empty)
    card = await p.build(ctx, "explicit_request")
    assert isinstance(card, HandoffCardBlock) and card.channels == []
    assert "+919876543210" in card.message
    assert ctx.state.handoff_offered is True


async def test_nothing_on_the_card_at_all_gives_no_card() -> None:
    p = HandoffCardProvider()
    bare = AssistantConfig.model_validate({"handoff": {"channels": []}})
    assert await p.build(_ctx(config=bare), "explicit_request") is None


# /TV1 human


async def test_api_surfaces_notify_on_an_explicit_offer_only() -> None:
    p = HandoffCardProvider()
    with patch("callobuzz_rag.conversation.handoff.provider._enqueue_offered") as enqueue:
        await p.build(_ctx(channel=Channel.API), "explicit_request")
        assert enqueue.call_count == 1
        payload = enqueue.call_args.args[0]
        assert payload["reason"] == "explicit_request" and payload["sources"] == [
            "refund-policy.pdf"
        ]
        assert payload["page_url"] == "https://shree-labs.in/tests"
        await p.build(_ctx(channel=Channel.STREAM, frustrated=True), "frustration")
        await p.build(_ctx(channel=Channel.PLAYGROUND, is_preview=True), "explicit_request")
        await p.build(_ctx(channel=Channel.WIDGET), "explicit_request")
        assert enqueue.call_count == 1
        await p.build(_ctx(channel=Channel.CONVERSATION), "explicit_request")
        assert enqueue.call_count == 2


# TV2 reference
def _decoded_wa(card: HandoffCardBlock) -> str:
    from urllib.parse import parse_qs, urlsplit

    url = next(c.url for c in card.channels if c.kind == "whatsapp")
    return parse_qs(urlsplit(url).query)["text"][0]


async def test_card_with_a_reference_prefills_whatsapp_and_names_it_for_calls() -> None:
    p = HandoffCardProvider()
    ctx = replace(
        _ctx(label=TurnLabel.BUSINESS_QUESTION, text="Sunday open?"),
        ref_code="SD-4F7K",
        question="Sunday open?",
        state=ConversationState(recent_questions=["Root canal ka kitna charge hai?"]),
    )
    with patch("callobuzz_rag.conversation.handoff.provider._enqueue_offered"):
        card = await p.build(ctx, "no_answer")
    assert isinstance(card, HandoffCardBlock)
    assert card.ref_code == "SD-4F7K"
    assert _decoded_wa(card) == "Ref SD-4F7K — Root canal ka kitna charge hai? · Sunday open?"
    assert "Your reference: **SD-4F7K** — quote it if you call." in card.message


async def test_a_request_for_a_person_is_not_a_question_in_the_prefill() -> None:
    p = HandoffCardProvider()
    ctx = replace(_ctx(), ref_code="SD-4F7K", question="I'd like to talk to a person")
    with patch("callobuzz_rag.conversation.handoff.provider._enqueue_offered"):
        card = await p.build(ctx, "explicit_request")
    assert isinstance(card, HandoffCardBlock)
    assert _decoded_wa(card) == "Ref SD-4F7K"


async def test_reference_line_only_when_a_call_option_is_on_the_card() -> None:
    p = HandoffCardProvider()
    only_wa = AssistantConfig.model_validate(
        {**CFG.model_dump(mode="json"), "handoff": {"channels": [{"kind": "whatsapp"}]}}
    )
    ctx = replace(_ctx(config=only_wa), ref_code="SD-4F7K")
    with patch("callobuzz_rag.conversation.handoff.provider._enqueue_offered"):
        card = await p.build(ctx, "explicit_request")
    assert isinstance(card, HandoffCardBlock)
    assert "reference" not in card.message.lower()
    assert card.ref_code == "SD-4F7K"


async def test_without_a_reference_the_card_is_unchanged() -> None:
    p = HandoffCardProvider()
    with patch("callobuzz_rag.conversation.handoff.provider._enqueue_offered"):
        card = await p.build(_ctx(), "explicit_request")
    assert isinstance(card, HandoffCardBlock)
    assert card.ref_code is None
    assert "reference" not in card.message.lower()
    assert _decoded_wa(card).startswith("Hi!")


async def test_localised_reference_lines() -> None:
    p = HandoffCardProvider()
    for lang, needle in (("hi", "संदर्भ"), ("hi-Latn", "Aapka reference")):
        ctx = replace(_ctx(), ref_code="SD-4F7K", language=lang)
        with patch("callobuzz_rag.conversation.handoff.provider._enqueue_offered"):
            card = await p.build(ctx, "explicit_request")
        assert isinstance(card, HandoffCardBlock)
        assert needle in card.message


def test_older_widgets_ignore_the_extra_field() -> None:
    block = HandoffCardBlock(open_now=True, message="x", channels=[], reason="r", ref_code="A-1")
    dumped = block.model_dump(mode="json")
    legacy = {k: v for k, v in dumped.items() if k != "ref_code"}
    assert set(dumped) - set(legacy) == {"ref_code"}


async def test_the_contact_only_card_shows_the_reference_in_its_text() -> None:
    p = HandoffCardProvider()
    empty = CFG.model_copy(update={"handoff": CFG.handoff.model_copy(update={"channels": []})})
    ctx = replace(_ctx(config=empty), ref_code="SD-4F7K")
    card = await p.build(ctx, "explicit_request")
    assert isinstance(card, HandoffCardBlock) and card.channels == []
    assert "+919876543210" in card.message
    assert "**SD-4F7K**" in card.message


# /TV2 reference
