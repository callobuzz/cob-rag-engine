# tests/unit/test_conv_state_offers.py
"""C1: per-conversation memory, office hours and the offer registry seam."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from callobuzz_rag.conversation.offers import (
    OfferContext,
    OfferKind,
    OfferRegistry,
    PreTurnContext,
    PreTurnResult,
)
from callobuzz_rag.conversation.signals import TurnSignals, office_state
from callobuzz_rag.conversation.state import ConversationState, load_state, save_state
from callobuzz_rag.conversation.types import Channel, OfficeState, Route, TurnLabel
from tests.unit.conversation_helpers import make_config


class TestState:
    def test_round_trip_and_unknown_keys_dropped(self) -> None:
        state = ConversationState.from_dict({"off_topic_strikes": 2, "junk": 1})
        assert state.off_topic_strikes == 2
        assert "junk" not in state.to_dict()
        assert ConversationState.from_dict(None).message_count == 0

    def test_every_layer_key_exists_from_the_start(self) -> None:
        keys = set(ConversationState().to_dict())
        assert {
            "lead_form_ids",
            "lead_offer_reason",
            "lead_capture",
            "last_handoff_reason",
            "chip_clicks",
            "cta_clicks",
        } <= keys

    def test_load_and_save_on_a_conversation(self) -> None:
        conv = SimpleNamespace(assistant_state={"off_topic_strikes": 1})
        state = load_state(conv)  # type: ignore[arg-type]
        assert state.off_topic_strikes == 1
        state.chip_clicks = 2
        save_state(conv, state)  # type: ignore[arg-type]
        assert conv.assistant_state["chip_clicks"] == 2
        assert load_state(None).message_count == 0

    def test_after_turn_counts(self) -> None:
        s = ConversationState()
        s.after_turn(label=TurnLabel.BUSINESS_QUESTION, route=Route.NO_ANSWER, gate_passed=False)
        s.after_turn(label=TurnLabel.BUSINESS_QUESTION, route=Route.NO_ANSWER, gate_passed=False)
        assert s.unresolved_turns == 2 and s.low_conf_streak == 2 and s.message_count == 2
        s.after_turn(label=TurnLabel.BUSINESS_QUESTION, route=Route.GROUNDED, gate_passed=True)
        assert s.low_conf_streak == 0 and s.unresolved_turns == 0
        assert s.last_label == "business_question"


class TestOfficeState:
    CARD = make_config().identity.card  # Mon-Sat 07:00-20:00, Asia/Kolkata

    def test_open_and_closed_in_ist(self) -> None:
        # 2026-10-05 is a Monday. 04:00 UTC = 09:30 IST.
        assert office_state(self.CARD, datetime(2026, 10, 5, 4, 0, tzinfo=UTC)) == OfficeState.OPEN
        # 15:00 UTC = 20:30 IST.
        assert (
            office_state(self.CARD, datetime(2026, 10, 5, 15, 0, tzinfo=UTC)) == OfficeState.CLOSED
        )
        # Sunday 2026-10-04 10:00 IST
        assert (
            office_state(self.CARD, datetime(2026, 10, 4, 4, 30, tzinfo=UTC)) == OfficeState.CLOSED
        )

    def test_holiday_is_closed(self) -> None:
        card = make_config(
            identity={
                "card": {
                    **self.CARD.model_dump(),
                    "holidays": [{"date": "2026-10-05", "name": "Dasara"}],
                }
            }
        ).identity.card
        assert office_state(card, datetime(2026, 10, 5, 4, 0, tzinfo=UTC)) == OfficeState.CLOSED

    def test_no_hours_is_unknown(self) -> None:
        card = self.CARD.model_copy(update={"hours": []})
        assert office_state(card, datetime(2026, 10, 5, 4, 0, tzinfo=UTC)) == OfficeState.UNKNOWN

    def test_several_ranges_a_day(self) -> None:
        card = type(self.CARD).model_validate(
            {
                **self.CARD.model_dump(),
                "hours": [
                    {"day": "mon", "open": "09:00", "close": "13:00"},
                    {"day": "mon", "open": "16:00", "close": "20:00"},
                ],
            }
        )
        # Monday 10:00 IST open, 14:00 IST lunch break closed, 17:00 IST open.
        assert office_state(card, datetime(2026, 10, 5, 4, 30, tzinfo=UTC)) == OfficeState.OPEN
        assert office_state(card, datetime(2026, 10, 5, 8, 30, tzinfo=UTC)) == OfficeState.CLOSED
        assert office_state(card, datetime(2026, 10, 5, 11, 30, tzinfo=UTC)) == OfficeState.OPEN

    def test_overnight_range_runs_past_midnight(self) -> None:
        # A restaurant open Friday 18:00 to 02:00 (Saturday morning).
        card = type(self.CARD).model_validate(
            {
                **self.CARD.model_dump(),
                "hours": [
                    {"day": "fri", "open": "18:00", "close": "02:00"},
                ],
            }
        )
        # Fri 2026-10-09 23:30 IST = 18:00 UTC -> open
        assert office_state(card, datetime(2026, 10, 9, 18, 0, tzinfo=UTC)) == OfficeState.OPEN
        # Sat 2026-10-10 01:30 IST = Fri 20:00 UTC -> still open
        assert office_state(card, datetime(2026, 10, 9, 20, 0, tzinfo=UTC)) == OfficeState.OPEN
        # Sat 02:30 IST -> closed; Fri 17:00 IST -> closed
        assert office_state(card, datetime(2026, 10, 9, 21, 0, tzinfo=UTC)) == OfficeState.CLOSED
        assert office_state(card, datetime(2026, 10, 9, 11, 30, tzinfo=UTC)) == OfficeState.CLOSED


class TestSignals:
    def test_for_test_fills_defaults(self) -> None:
        s = TurnSignals.for_test(label=TurnLabel.HUMAN_REQUEST, asked_for_contact=True)
        assert s.label == TurnLabel.HUMAN_REQUEST and s.asked_for_contact
        assert s.abuse_level == 0 and s.blocked_topic_id is None and not s.is_preview
        assert s.now.tzinfo is not None and s.route == Route.GROUNDED


def _ctx(**overrides: Any) -> OfferContext:
    signals = TurnSignals(
        label=TurnLabel.BUYING_INTENT,
        label_confidence=0.9,
        topic_id=None,
        frustrated=False,
        needs_human=False,
        route=Route.GROUNDED,
        gate_passed=True,
        top_score=0.7,
        no_answer=False,
        office=OfficeState.OPEN,
        page_url=None,
        language="en",
        message_count=1,
        keyword_hits=[],
        text="book a test",
    )
    values: dict[str, Any] = {
        "config": make_config(),
        "signals": signals,
        "state": ConversationState(),
        "language": "en",
        "corpus_id": uuid.uuid4(),
        "conversation_id": None,
        "channel": Channel.WIDGET,
        "answer_text": "Sure.",
        "cited_sources": [],
    }
    values.update(overrides)
    return OfferContext(**values)


@dataclass
class _Provider:
    kind: OfferKind
    reason: str | None
    value: Any

    def wanted(self, ctx: OfferContext) -> str | None:
        return self.reason

    async def build(self, ctx: OfferContext, reason: str) -> Any:
        return self.value


class TestRegistry:
    async def test_empty_registry_offers_nothing(self) -> None:
        out = await OfferRegistry().collect(_ctx(), ui_allowed=True)
        assert out.ui is None and out.cta is None and out.quick_replies == []

    async def test_handoff_beats_lead_and_suppresses_cta(self) -> None:
        reg = OfferRegistry()
        reg.register(_Provider(OfferKind.LEAD_FORM, "buying_intent", {"kind": "lead_form"}))
        reg.register(
            _Provider(OfferKind.HANDOFF_CARD, "explicit_request", {"kind": "handoff_card"})
        )
        reg.register(_Provider(OfferKind.CTA, "buying_intent", {"type": "call"}))
        reg.register(_Provider(OfferKind.QUICK_REPLIES, "topic", [{"label": "Prices"}]))
        out = await reg.collect(_ctx(), ui_allowed=True)
        assert out.ui == {"kind": "handoff_card"} and out.cta is None
        assert out.quick_replies == [{"label": "Prices"}]
        assert ("handoff_card", "explicit_request") in out.reasons

    async def test_ui_not_allowed_keeps_cta(self) -> None:
        reg = OfferRegistry()
        reg.register(_Provider(OfferKind.LEAD_FORM, "buying_intent", {"kind": "lead_form"}))
        reg.register(_Provider(OfferKind.CTA, "buying_intent", {"type": "call"}))
        out = await reg.collect(_ctx(), ui_allowed=False)
        assert out.ui is None and out.cta == {"type": "call"}

    async def test_forced_kind_asks_only_that_provider(self) -> None:
        reg = OfferRegistry()
        reg.register(_Provider(OfferKind.LEAD_FORM, None, {"kind": "lead_form"}))
        out = await reg.collect(_ctx(), ui_allowed=True, forced=OfferKind.LEAD_FORM)
        assert out.ui == {"kind": "lead_form"}
        assert ("lead_form", "forced") in out.reasons

    async def test_a_string_offer_is_a_paragraph_and_takes_the_ui_slot(self) -> None:
        reg = OfferRegistry()
        reg.register(
            _Provider(
                OfferKind.LEAD_FORM,
                "buying_intent",
                "Could you share your phone number so we can call you?",
            )
        )
        reg.register(_Provider(OfferKind.CTA, "buying_intent", {"type": "call"}))
        out = await reg.collect(_ctx(), ui_allowed=True)
        assert out.ui is None and out.cta is None
        assert out.paragraphs == ["Could you share your phone number so we can call you?"]

    async def test_second_pass_sees_ui_shown_and_missing_fallback(self) -> None:
        seen: list[tuple[bool, bool]] = []

        class _Cta:
            kind = OfferKind.CTA

            def wanted(self, ctx: OfferContext) -> str | None:
                seen.append((ctx.ui_shown, ctx.ui_missing_for_fallback))
                return None

            async def build(self, ctx: OfferContext, reason: str) -> Any:
                return None

        reg = OfferRegistry()
        reg.register(_Cta())
        no_answer = TurnSignals.for_test(no_answer=True, route=Route.NO_ANSWER)
        await reg.collect(_ctx(signals=no_answer), ui_allowed=True)
        assert seen == [(False, True)]

    async def test_pre_turn_hooks_are_registered_in_order(self) -> None:
        reg = OfferRegistry()

        async def first(ctx: PreTurnContext) -> PreTurnResult | None:
            return None

        async def second(ctx: PreTurnContext) -> PreTurnResult | None:
            return PreTurnResult(stored_text="[phone shared]", reply="Thanks!", quick_replies=[])

        reg.register_pre_turn(first)
        reg.register_pre_turn(second)
        assert reg.pre_turn_hooks() == [first, second]
