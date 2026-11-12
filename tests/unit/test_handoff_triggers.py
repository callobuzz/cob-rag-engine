"""When to offer a person (C5): explicit asks, blocked topics, abuse, keywords, frustration,
low confidence, unresolved turns, out of hours: in that order, without nagging."""

from dataclasses import replace

import pytest

from callobuzz_rag.conversation.handoff.triggers import (
    HandoffReason,
    forced_reason,
    handoff_reason,
    keyword_hits,
)
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.signals import TurnSignals
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.types import OfficeState, Route, TurnLabel


def _signals(**kw: object) -> TurnSignals:
    base: dict[str, object] = {
        "label": TurnLabel.BUSINESS_QUESTION,
        "text": "What are the fasting rules for a lipid profile?",
        "gate_passed": True,
        "office": OfficeState.OPEN,
    }
    base.update(kw)
    return TurnSignals.for_test(**base)


CFG = AssistantConfig()
# TV1: a miss offers a person first by default; the older triggers are tested with that off
NO_MISSES = AssistantConfig.model_validate({"handoff": {"triggers": {"offer_after_misses": 0}}})


def test_explicit_request_by_label_or_flag() -> None:
    explicit = HandoffReason.EXPLICIT_REQUEST
    human = _signals(label=TurnLabel.HUMAN_REQUEST)
    assert handoff_reason(CFG, human, ConversationState()) is explicit
    assert handoff_reason(CFG, _signals(needs_human=True), ConversationState()) is explicit


def test_disabled_handoff_never_fires() -> None:
    cfg = AssistantConfig.model_validate({"handoff": {"enabled": False}})
    human = _signals(label=TurnLabel.HUMAN_REQUEST)
    assert handoff_reason(cfg, human, ConversationState()) is None


def test_blocked_topic_only_with_handoff_action() -> None:
    s = _signals(blocked_topic_id="diagnosis", blocked_action="handoff")
    assert handoff_reason(CFG, s, ConversationState()) is HandoffReason.BLOCKED_TOPIC
    assert handoff_reason(CFG, replace(s, blocked_action="decline"), ConversationState()) is None


def test_abuse_is_the_second_strike_routed_to_handoff() -> None:
    warned = _signals(label=TurnLabel.ABUSE, route=Route.ABUSE_WARNING, abuse_level=2)
    assert handoff_reason(CFG, warned, ConversationState()) is None
    second = _signals(label=TurnLabel.ABUSE, route=Route.HANDOFF)
    assert handoff_reason(CFG, second, ConversationState()) is HandoffReason.ABUSE


@pytest.mark.parametrize(
    "text",
    [
        "I want a refund now",
        "please CANCEL my booking",
        "मेरी शिकायत है",
        "paise wapas karo",
        "Refund chahiye",
    ],
)
def test_keywords_en_hi_hinglish(text: str) -> None:
    assert handoff_reason(CFG, _signals(text=text), ConversationState()) is HandoffReason.KEYWORD


def test_latin_keywords_match_whole_words_only() -> None:
    assert keyword_hits("Our cancellation policy page", ["cancel"]) == []
    assert keyword_hits("cancel.", ["cancel"]) == ["cancel"]
    assert keyword_hits("a refunded policy page", ["refund"]) == []


def test_frustration() -> None:
    s = _signals(frustrated=True)
    assert handoff_reason(CFG, s, ConversationState()) is HandoffReason.FRUSTRATION


def test_low_confidence_counts_this_turn() -> None:
    """The registry is asked before the counters move: one earlier miss plus this one."""
    one_before = ConversationState(low_conf_streak=1)
    miss = _signals(gate_passed=False)
    assert handoff_reason(NO_MISSES, miss, one_before) is HandoffReason.LOW_CONFIDENCE
    assert handoff_reason(NO_MISSES, _signals(gate_passed=True), one_before) is None
    assert handoff_reason(NO_MISSES, miss, ConversationState()) is None


def test_unresolved_counts_this_turn() -> None:
    no_answer = _signals(route=Route.NO_ANSWER, gate_passed=None)
    assert (
        handoff_reason(NO_MISSES, no_answer, ConversationState(unresolved_turns=2))
        is HandoffReason.UNRESOLVED
    )
    assert handoff_reason(NO_MISSES, no_answer, ConversationState(unresolved_turns=1)) is None
    # an answered turn resets the run, so it never fires on a grounded answer
    assert handoff_reason(NO_MISSES, _signals(), ConversationState(unresolved_turns=5)) is None


def test_out_of_hours_needs_the_switch_and_a_reason_to_interrupt() -> None:
    cfg = AssistantConfig.model_validate(
        {"handoff": {"triggers": {"out_of_hours": True, "offer_after_misses": 0}}}
    )
    closed = _signals(office=OfficeState.CLOSED)
    out = HandoffReason.OUT_OF_HOURS
    assert handoff_reason(cfg, closed, ConversationState()) is None  # an answered question
    assert handoff_reason(cfg, replace(closed, gate_passed=False), ConversationState()) is out
    buying = replace(closed, label=TurnLabel.BUYING_INTENT)
    assert handoff_reason(cfg, buying, ConversationState()) is out
    assert (
        handoff_reason(NO_MISSES, replace(closed, gate_passed=False), ConversationState()) is None
    )
    unknown = _signals(office=OfficeState.UNKNOWN, gate_passed=False)
    assert handoff_reason(cfg, unknown, ConversationState()) is None


def test_no_nagging_after_an_offer() -> None:
    offered = ConversationState(handoff_offered=True, low_conf_streak=3)
    assert handoff_reason(CFG, _signals(frustrated=True), offered) is None
    human = _signals(label=TurnLabel.HUMAN_REQUEST)
    assert handoff_reason(CFG, human, offered) is HandoffReason.EXPLICIT_REQUEST


def test_order_explicit_beats_keyword() -> None:
    s = _signals(label=TurnLabel.HUMAN_REQUEST, text="refund please")
    assert handoff_reason(CFG, s, ConversationState()) is HandoffReason.EXPLICIT_REQUEST


def test_forced_reason_branches() -> None:
    assert forced_reason(_signals(label=TurnLabel.ABUSE)) is HandoffReason.ABUSE
    assert (
        forced_reason(_signals(blocked_topic_id="x", blocked_action="handoff"))
        is HandoffReason.BLOCKED_TOPIC
    )
    assert forced_reason(_signals(label=TurnLabel.HUMAN_REQUEST)) is HandoffReason.EXPLICIT_REQUEST
    assert forced_reason(_signals(needs_human=True)) is HandoffReason.EXPLICIT_REQUEST
    assert forced_reason(_signals(route=Route.NO_ANSWER)) is HandoffReason.NO_ANSWER
