"""Quick-reply and CTA offer providers, and the director with them registered (C6)."""

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

from callobuzz_rag.conversation.envelope import DoneEvent, QuickRepliesEvent, TokenEvent
from callobuzz_rag.conversation.offers import OfferContext, OfferKind, OfferRegistry
from callobuzz_rag.conversation.replies import (
    CtaProvider,
    QuickRepliesProvider,
    register_reply_providers,
)
from callobuzz_rag.conversation.schema import AssistantConfig, QuickReply
from callobuzz_rag.conversation.signals import TurnSignals
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.types import Channel, Route, TurnLabel
from tests.unit.conversation_helpers import FakeLLM, make_director, make_turn, run_events
from tests.unit.replies_helpers import replies_config as _config
from tests.unit.replies_helpers import sales_config


def _ctx(cfg: AssistantConfig, label: TurnLabel, route: Route, **kw: Any) -> OfferContext:
    signals = TurnSignals.for_test(label=label, route=route, topic_id="tests")
    base: dict[str, Any] = {
        "config": cfg,
        "signals": signals,
        "state": ConversationState(),
        "language": "en",
        "corpus_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "channel": Channel.WIDGET,
        "answer_text": "Home collection is free above Rs 999 [2].",
        "cited_sources": [{"index": 2, "section": "Charges", "file_name": "home-collection.pdf"}],
        "question": "Is home collection free?",
    }
    base.update(kw)
    return OfferContext(**base)


def test_registration_is_idempotent() -> None:
    reg = OfferRegistry()
    register_reply_providers(reg)
    register_reply_providers(reg)
    assert len(reg.providers(OfferKind.QUICK_REPLIES)) == 1
    assert len(reg.providers(OfferKind.CTA)) == 1


def test_the_default_registry_has_both_after_importing_the_layer() -> None:
    import callobuzz_rag.conversation  # noqa: F401
    from callobuzz_rag.conversation.offers import registry

    assert registry.has(OfferKind.QUICK_REPLIES) and registry.has(OfferKind.CTA)


async def test_quick_replies_manual_first_then_ai() -> None:
    llm = AsyncMock()
    llm.has_credentials = True
    provider = QuickRepliesProvider()
    ctx = _ctx(_config(), TurnLabel.BUSINESS_QUESTION, Route.GROUNDED, llm=llm, probe=None)
    ai = AsyncMock(return_value=[QuickReply(label="Home pickup", message="Is home pickup free?")])
    with patch("callobuzz_rag.conversation.replies.ai_followups", ai):
        reason = provider.wanted(ctx)
        chips = await provider.build(ctx, reason or "")
    assert reason == "answer"
    assert [c.label for c in chips] == ["Fasting rules", "Report time", "Home pickup"]
    kwargs = ai.await_args.kwargs
    assert kwargs["question"] == "Is home collection free?" and kwargs["sections"] == ["Charges"]


async def test_quick_replies_route_rules() -> None:
    provider = QuickRepliesProvider()
    assert provider.wanted(_ctx(_config(), TurnLabel.ABUSE, Route.ABUSE_WARNING)) is None
    assert provider.wanted(_ctx(_config(), TurnLabel.BUSINESS_QUESTION, Route.HANDOFF)) is None
    decline = _ctx(_config(), TurnLabel.OFF_TOPIC, Route.DECLINE)
    assert provider.wanted(decline) == "decline_pivot"
    chips = await provider.build(decline, "decline_pivot")
    assert [c.label for c in chips] == ["Blood tests", "Home collection"]


async def test_quick_replies_no_answer_only_with_topic_chips_and_smalltalk_starters() -> None:
    provider = QuickRepliesProvider()
    miss = _ctx(_config(), TurnLabel.BUSINESS_QUESTION, Route.NO_ANSWER)
    assert provider.wanted(miss) == "no_answer_topic"
    chips = await provider.build(miss, "no_answer_topic")
    assert [c.label for c in chips] == ["Fasting rules", "Report time"]
    cfg = _config()
    assert provider.wanted(_ctx(cfg, TurnLabel.SMALLTALK, Route.SMALLTALK)) is None
    cfg.conversation.starters = [QuickReply(label="Prices", message="What are your prices?")]
    hello = _ctx(cfg, TurnLabel.SMALLTALK, Route.SMALLTALK)
    assert provider.wanted(hello) == "starters"
    assert [c.label for c in await provider.build(hello, "starters")] == ["Prices"]


async def test_cta_provider_respects_ui_and_declines() -> None:
    provider = CtaProvider()
    ctx = _ctx(sales_config(), TurnLabel.BUYING_INTENT, Route.GROUNDED)
    assert provider.wanted(ctx) == "buying_intent"
    cta = await provider.build(ctx, "buying_intent")
    assert cta is not None and cta.type == "whatsapp"
    assert ctx.state.cta_shown == ["whatsapp"]
    shown = _ctx(sales_config(), TurnLabel.BUYING_INTENT, Route.GROUNDED, ui_shown=True)
    assert provider.wanted(shown) is None
    state = ConversationState()
    state.decline_cta("whatsapp")
    again = _ctx(sales_config(), TurnLabel.BUYING_INTENT, Route.GROUNDED, state=state)
    assert await provider.build(again, "buying_intent") is None


_BUY = {
    "label": "buying_intent",
    "confidence": 0.9,
    "language": "en",
    "topic": None,
    "frustrated": False,
    "needs_human": False,
}


async def test_director_emits_chips_after_the_last_token_and_the_cta_on_done(
    director_io: Any,
) -> None:
    reg = OfferRegistry()
    register_reply_providers(reg)
    llm = FakeLLM(
        json_replies=[_BUY, {"questions": ["Which tests need fasting?"]}],
        stream=["Home sample collection costs Rs 150 within Pune city [1]."],
    )
    events = await run_events(
        make_director(llm, registry=reg),
        make_turn(
            "How much is home collection?",
            conversation={"cta": {"buying_intent_cta": "whatsapp"}},
        ),
    )
    kinds = [type(e) for e in events]
    last_token = max(i for i, k in enumerate(kinds) if k is TokenEvent)
    assert kinds.index(QuickRepliesEvent) > last_token
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.cta is not None and done.cta.type == "whatsapp"
    assert done.cta.url is not None and done.cta.url.startswith("https://wa.me/")
    assert 0 < len(done.quick_replies) <= 4
    rec = director_io.persist.await_args.args[1]
    assert "cta:whatsapp" in rec.offers and "quick_replies" in rec.offers
    assert rec.state.cta_shown == ["whatsapp"]


async def test_a_free_verbatim_turn_with_a_followup_call_is_not_a_billed_question(
    director_io: Any,
) -> None:
    reg = OfferRegistry()
    register_reply_providers(reg)
    llm = FakeLLM(json_replies=[{"questions": ["Which tests need fasting?"]}])
    await run_events(make_director(llm, registry=reg), make_turn("I want to talk to a human"))
    assert len(llm.calls) == 1  # the follow-up call, nothing before it
    kwargs = director_io.meter.await_args.kwargs
    assert kwargs["requests"] == 0
    assert kwargs["cost"].calls == 1 and kwargs["cost"].cost_usd > 0
