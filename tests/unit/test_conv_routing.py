# tests/unit/test_conv_routing.py
"""C1: label + gate + state + config → route (design §5)."""

import pytest

from callobuzz_rag.conversation.gates import GateResult
from callobuzz_rag.conversation.labeler import LabelResult
from callobuzz_rag.conversation.offers import OfferKind
from callobuzz_rag.conversation.routing import decide_route, needs_retrieval
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.types import Route, TurnLabel
from tests.unit.conversation_helpers import make_config

PASS = GateResult(True, 0.71, 0.5, "measured", 4)
FAIL = GateResult(False, 0.31, 0.5, "measured", 4)


def _label(
    label: TurnLabel, confidence: float = 0.9, topic: str | None = None, needs_human: bool = False
) -> LabelResult:
    return LabelResult(label, "model", confidence, topic_id=topic, needs_human=needs_human)


def _route(
    label: LabelResult,
    gate: GateResult | None = PASS,
    *,
    state: ConversationState | None = None,
    has_handoff: bool = True,
    **cfg: object,
):
    return decide_route(
        label=label,
        gate=gate,
        config=make_config(**cfg),  # type: ignore[arg-type]
        state=state or ConversationState(),
        has_handoff=has_handoff,
    )


def test_business_question_grounded_or_no_answer() -> None:
    assert _route(_label(TurnLabel.BUSINESS_QUESTION)).route == Route.GROUNDED
    assert _route(_label(TurnLabel.BUSINESS_QUESTION), FAIL).route == Route.NO_ANSWER
    assert _route(_label(TurnLabel.BUSINESS_QUESTION), None).route == Route.GROUNDED  # planned


@pytest.mark.parametrize("label", [TurnLabel.BUYING_INTENT, TurnLabel.OTHER])
def test_buying_intent_and_other_take_the_grounded_path(label: TurnLabel) -> None:
    assert _route(_label(label)).route == Route.GROUNDED


def test_smalltalk() -> None:
    assert _route(_label(TurnLabel.SMALLTALK), None).route == Route.SMALLTALK


def test_off_topic_declines_and_strikes() -> None:
    d = _route(_label(TurnLabel.OFF_TOPIC), FAIL)
    assert d.route == Route.DECLINE and d.strike == "off_topic" and not d.stop


def test_third_strike_stops() -> None:
    state = ConversationState(off_topic_strikes=2)
    d = _route(_label(TurnLabel.OFF_TOPIC), FAIL, state=state)
    assert d.route == Route.DECLINE and d.stop and d.reason == "strikes"


def test_tie_rule_low_confidence_off_topic_with_docs_is_business() -> None:
    d = _route(_label(TurnLabel.OFF_TOPIC, confidence=0.55), PASS)
    assert d.route == Route.GROUNDED and d.reason == "tie"
    assert _route(_label(TurnLabel.OFF_TOPIC, confidence=0.9), PASS).route == Route.DECLINE


def test_open_with_guardrails_explains_off_topic() -> None:
    d = _route(_label(TurnLabel.OFF_TOPIC), FAIL, scope={"scope_mode": "open_with_guardrails"})
    assert d.route == Route.EXPLAINER


def test_domain_explainer_modes() -> None:
    assert _route(_label(TurnLabel.DOMAIN_EXPLAINER), PASS).route == Route.GROUNDED
    assert _route(_label(TurnLabel.DOMAIN_EXPLAINER), FAIL).route == Route.EXPLAINER
    strict = _route(_label(TurnLabel.DOMAIN_EXPLAINER), FAIL, scope={"scope_mode": "strict_docs"})
    assert strict.route == Route.NO_ANSWER


def test_abuse_warns_then_hands_off() -> None:
    first = _route(_label(TurnLabel.ABUSE), None)
    assert first.route == Route.ABUSE_WARNING and first.strike == "abuse"
    second = _route(_label(TurnLabel.ABUSE), None, state=ConversationState(abuse_strikes=1))
    assert second.route == Route.HANDOFF and second.forced_offer == OfferKind.HANDOFF_CARD
    no_handoff = _route(
        _label(TurnLabel.ABUSE), None, state=ConversationState(abuse_strikes=1), has_handoff=False
    )
    assert no_handoff.route == Route.ABUSE_WARNING


def test_human_request() -> None:
    d = _route(_label(TurnLabel.HUMAN_REQUEST), None)
    assert d.route == Route.HANDOFF and d.reason == "explicit_request"
    assert _route(_label(TurnLabel.BUSINESS_QUESTION, needs_human=True)).route == Route.HANDOFF
    assert _route(_label(TurnLabel.HUMAN_REQUEST), None, has_handoff=False).route == Route.VERBATIM


def test_blocked_topic() -> None:
    blocked = {
        "blocked_topics": [
            {"id": "diagnosis", "name": "Diagnosis", "description": "d", "action": "handoff"}
        ]
    }
    d = _route(_label(TurnLabel.BUSINESS_QUESTION, topic="diagnosis"), PASS, scope=blocked)
    assert d.route == Route.HANDOFF and d.reason == "blocked_topic"
    decline = {"blocked_topics": [{"id": "diagnosis", "name": "Diagnosis", "description": "d"}]}
    assert (
        _route(_label(TurnLabel.BUSINESS_QUESTION, topic="diagnosis"), PASS, scope=decline).route
        == Route.DECLINE
    )


def test_verbatim_topic_skips_retrieval() -> None:
    cfg_scope = {
        "topics": [
            {
                "id": "fees",
                "name": "Fees",
                "description": "f",
                "verbatim_answer": {"en": "CBC costs ₹350."},
            }
        ]
    }
    label = _label(TurnLabel.BUSINESS_QUESTION, topic="fees")
    assert not needs_retrieval(label, make_config(scope=cfg_scope))
    assert _route(label, None, scope=cfg_scope).route == Route.VERBATIM


def test_stopped_conversation_only_declines() -> None:
    d = _route(_label(TurnLabel.BUSINESS_QUESTION), PASS, state=ConversationState(stopped=True))
    assert d.route == Route.DECLINE and d.reason == "stopped"


def test_needs_retrieval() -> None:
    cfg = make_config()
    assert needs_retrieval(_label(TurnLabel.BUSINESS_QUESTION), cfg)
    assert needs_retrieval(_label(TurnLabel.DOMAIN_EXPLAINER), cfg)
    assert needs_retrieval(_label(TurnLabel.OFF_TOPIC, confidence=0.5), cfg)
    assert not needs_retrieval(_label(TurnLabel.OFF_TOPIC, confidence=0.9), cfg)
    assert not needs_retrieval(_label(TurnLabel.SMALLTALK), cfg)
    assert not needs_retrieval(_label(TurnLabel.HUMAN_REQUEST), cfg)
    assert not needs_retrieval(_label(TurnLabel.ABUSE), cfg)


# TV1 human
def test_human_request_beats_the_off_topic_stop() -> None:
    stopped = ConversationState(off_topic_strikes=3, stopped=True)
    d = _route(_label(TurnLabel.HUMAN_REQUEST), state=stopped)
    assert (d.route, d.reason, d.forced_offer) == (
        Route.HANDOFF,
        "explicit_request",
        OfferKind.HANDOFF_CARD,
    )


def test_human_request_when_stopped_without_a_card_gives_the_contact_line() -> None:
    d = _route(
        _label(TurnLabel.HUMAN_REQUEST), state=ConversationState(stopped=True), has_handoff=False
    )
    assert d.route == Route.VERBATIM


def test_still_stopped_for_anything_else() -> None:
    d = _route(_label(TurnLabel.BUSINESS_QUESTION), state=ConversationState(stopped=True))
    assert d.route == Route.DECLINE and d.reason == "stopped"


def test_abuse_still_first() -> None:
    assert _route(_label(TurnLabel.ABUSE)).route == Route.ABUSE_WARNING


# /TV1 human
