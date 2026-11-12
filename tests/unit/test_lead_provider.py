import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet

from callobuzz_rag.conversation.leads.provider import (
    LEAD_FORM_PROVIDER,
    capture_contact_hook,
)
from callobuzz_rag.conversation.offers import OfferKind, PreTurnContext, registry
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.types import Channel, OfficeState, Route, TurnLabel
from callobuzz_rag.core.config import Settings

KEY = Fernet.generate_key().decode()
VERSION = AssistantConfig().leads.consent.notice_version


def _config(mode: str = "form", **cfg: object) -> AssistantConfig:
    raw = AssistantConfig().model_dump()
    raw["identity"]["business_name"] = "Shree Labs"
    raw["leads"]["mode"] = mode
    raw["leads"].update(cfg)
    return AssistantConfig.model_validate(raw)


def _ctx(mode: str = "form", *, channel: Channel = Channel.WIDGET, preview: bool = False) -> Any:
    signals = SimpleNamespace(
        label=TurnLabel.BUYING_INTENT,
        route=Route.GROUNDED,
        office=OfficeState.OPEN,
        page_url=None,
        topic_id=None,
        asked_for_contact=False,
        is_preview=preview,
    )
    return SimpleNamespace(
        config=_config(mode),
        signals=signals,
        state=ConversationState(message_count=2),
        language="hi-Latn",
        channel=channel,
    )


def test_registered_once_and_hook_registered() -> None:
    assert registry.providers(OfferKind.LEAD_FORM) == [LEAD_FORM_PROVIDER]
    assert capture_contact_hook in registry.pre_turn_hooks()


async def test_form_block_in_visitor_language() -> None:
    ctx = _ctx()
    assert LEAD_FORM_PROVIDER.wanted(ctx) == "buying_intent"
    block = await LEAD_FORM_PROVIDER.build(ctx, "buying_intent")
    assert block.kind == "lead_form" and block.form_id.startswith("lf_")
    assert [f.id for f in block.fields] == ["name", "phone", "email"]
    assert [f.type for f in block.fields] == ["text", "tel", "email"]
    assert [f.required for f in block.fields] == [False, True, False]
    assert "sahmati" in block.notice and block.submit_label == "Bhejein"
    assert ctx.state.lead_offers == 1 and ctx.state.lead_form_ids == [f"{block.form_id}:{VERSION}"]
    assert ctx.state.lead_offer_reason == "buying_intent"


async def test_only_the_last_five_form_ids_are_kept() -> None:
    ctx = _ctx()
    for _ in range(7):
        block = await LEAD_FORM_PROVIDER.build(ctx, "asked")
    assert (
        len(ctx.state.lead_form_ids) == 5
        and ctx.state.lead_form_ids[-1] == f"{block.form_id}:{VERSION}"
    )


async def test_forced_mode_reads_as_asked() -> None:
    ctx = _ctx()
    block = await LEAD_FORM_PROVIDER.build(ctx, "forced")
    assert block.trigger == "asked" and ctx.state.lead_offer_reason == "asked"


async def test_no_form_for_other_surfaces_previews_or_after_a_lead() -> None:
    for ctx in (_ctx(channel=Channel.API), _ctx(preview=True)):
        assert await LEAD_FORM_PROVIDER.build(ctx, "buying_intent") is None
        assert ctx.state.lead_offers == 0
    done = _ctx()
    done.state.lead_id = str(uuid.uuid4())
    assert await LEAD_FORM_PROVIDER.build(done, "forced") is None


async def test_custom_fields_follow_the_built_in_ones() -> None:
    ctx = _ctx()
    raw = AssistantConfig().model_dump()
    raw["leads"]["custom_fields"] = [
        {"id": "test_name", "label": {"en": "Test"}, "type": "select", "options": ["HbA1c"]}
    ]
    ctx.config = AssistantConfig.model_validate(raw)
    block = await LEAD_FORM_PROVIDER.build(ctx, "asked")
    assert [f.id for f in block.fields][-1] == "test_name"
    assert block.fields[-1].options == ["HbA1c"]


async def test_conversational_mode_asks_in_text() -> None:
    ctx = _ctx(mode="conversational")
    text = await LEAD_FORM_PROVIDER.build(ctx, "buying_intent")
    assert isinstance(text, str) and "number" in text.lower()
    assert ctx.state.lead_offers == 1


# ---- the pre-turn hook -----------------------------------------------------------------


def _pre(
    text: str,
    state: ConversationState,
    *,
    mode: str = "conversational",
    channel: Channel = Channel.WIDGET,
    preview: bool = False,
    key: str = KEY,
) -> PreTurnContext:
    return PreTurnContext(
        config=_config(mode),
        state=state,
        text=text,
        language="en",
        corpus_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        widget_key_id=None,
        channel=channel,
        page_url=None,
        utm=None,
        session_factory=lambda: None,  # type: ignore[arg-type,return-value]
        settings=Settings(_env_file=None, encryption_key=key),  # type: ignore[call-arg]
        now=datetime(2026, 10, 14, tzinfo=UTC),
        is_preview=preview,
    )


async def test_phone_is_captured_masked_and_consent_asked() -> None:
    state = ConversationState(lead_offers=1)
    result = await capture_contact_hook(_pre("sure, my number is 98765 43210", state))
    assert result is not None and result.stored_text == "sure, my number is [phone shared]"
    assert "agree" in (result.reply or "") and result.quick_replies[0].label == "Yes, I agree"
    assert result.quick_replies[1].label == "No"
    assert "9876543210" not in str(state.lead_capture)
    assert state.lead_capture is not None and state.lead_capture["awaiting_consent"] is True


async def test_notice_braces_do_not_break_the_consent_question() -> None:
    state = ConversationState(lead_offers=1)
    pre = _pre("9876543210", state)
    raw = pre.config.model_dump()
    raw["leads"]["consent"]["notice"] = {"en": "Kept {x} for {retention_days} days }"}
    object.__setattr__(pre, "config", AssistantConfig.model_validate(raw))
    result = await capture_contact_hook(pre)
    assert result is not None and "Kept {x} for 365 days }" in (result.reply or "")


async def test_yes_creates_the_lead() -> None:
    state = ConversationState(lead_offers=1)
    await capture_contact_hook(_pre("9876543210", state))
    with patch(
        "callobuzz_rag.conversation.leads.provider._create_from_capture",
        AsyncMock(return_value=uuid.uuid4()),
    ) as create:
        result = await capture_contact_hook(_pre("Yes, I agree", state))
    create.assert_awaited_once()
    clean = create.await_args.args[1]
    assert clean.phone == "+919876543210"
    assert result is not None and state.lead_id is not None and state.lead_capture is None


async def test_no_drops_the_capture_and_stops_asking() -> None:
    state = ConversationState(lead_offers=1)
    await capture_contact_hook(_pre("9876543210", state))
    assert await capture_contact_hook(_pre("No", state)) is None
    assert state.lead_capture is None and state.lead_dismissed is True


async def test_a_required_email_is_asked_for_before_consent() -> None:
    state = ConversationState(lead_offers=1)
    pre = _pre("9876543210", state)
    raw = pre.config.model_dump()
    raw["leads"]["email"] = "required"
    object.__setattr__(pre, "config", AssistantConfig.model_validate(raw))
    result = await capture_contact_hook(pre)
    assert result is not None and "e-mail" in (result.reply or "")
    assert result.quick_replies == [] and state.lead_capture["awaiting_consent"] is False  # type: ignore[index]


async def test_nothing_to_do_cases() -> None:
    assert await capture_contact_hook(_pre("9876543210", ConversationState(lead_offers=0))) is None
    assert await capture_contact_hook(_pre("hello there", ConversationState(lead_offers=1))) is None
    offered = ConversationState(lead_offers=1)
    assert await capture_contact_hook(_pre("9876543210", offered, mode="form")) is None
    assert await capture_contact_hook(_pre("9876543210", offered, channel=Channel.API)) is None
    assert await capture_contact_hook(_pre("9876543210", offered, preview=True)) is None
    assert await capture_contact_hook(_pre("9876543210", offered, key="")) is None
    done = ConversationState(lead_offers=1, lead_id="x")
    assert await capture_contact_hook(_pre("9876543210", done)) is None
    assert offered.lead_capture is None


async def test_a_capture_the_visitor_never_agreed_to_is_dropped_after_an_hour() -> None:
    from datetime import timedelta

    state = ConversationState(lead_offers=1)
    await capture_contact_hook(_pre("9876543210", state))
    assert state.lead_capture is not None and state.lead_capture["at"].startswith("2026-10-14")
    later = _pre("Yes, I agree", state)
    object.__setattr__(later, "now", later.now + timedelta(hours=1, minutes=1))
    with patch(
        "callobuzz_rag.conversation.leads.provider._create_from_capture", AsyncMock()
    ) as create:
        result = await capture_contact_hook(later)
    # the stale phone number is gone, no lead is made, and the turn goes on as a question
    create.assert_not_awaited()
    assert result is None and state.lead_capture is None and state.lead_id is None


async def test_a_capture_without_a_timestamp_counts_as_stale() -> None:
    state = ConversationState(lead_offers=1, lead_capture={"sealed": "x", "awaiting_consent": True})
    with patch(
        "callobuzz_rag.conversation.leads.provider._create_from_capture", AsyncMock()
    ) as create:
        result = await capture_contact_hook(_pre("Yes, I agree", state))
    create.assert_not_awaited()
    assert result is None and state.lead_capture is None


async def test_a_fresh_capture_still_works_within_the_hour() -> None:
    from datetime import timedelta

    state = ConversationState(lead_offers=1)
    await capture_contact_hook(_pre("9876543210", state))
    soon = _pre("Yes, I agree", state)
    object.__setattr__(soon, "now", soon.now + timedelta(minutes=59))
    with patch(
        "callobuzz_rag.conversation.leads.provider._create_from_capture",
        AsyncMock(return_value=uuid.uuid4()),
    ) as create:
        result = await capture_contact_hook(soon)
    create.assert_awaited_once()
    assert result is not None and state.lead_id is not None
