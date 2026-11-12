# src/callobuzz_rag/conversation/routing.py
"""Label + gate + conversation state + configuration → route (design §5). Pure.

Called twice per turn: with gate=None for the planned route (sent in `meta`), then with
the gate result for the final one. Business facts only ever come from Route.GROUNDED or
Route.VERBATIM (owner-written text); every other route states no business specifics.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from callobuzz_rag.conversation.offers import OfferKind
from callobuzz_rag.conversation.types import Route, TurnLabel

if TYPE_CHECKING:
    from callobuzz_rag.conversation.gates import GateResult
    from callobuzz_rag.conversation.labeler import LabelResult
    from callobuzz_rag.conversation.schema import AssistantConfig
    from callobuzz_rag.conversation.state import ConversationState

TIE_CONFIDENCE = 0.6
_BUSINESS = (TurnLabel.BUSINESS_QUESTION, TurnLabel.BUYING_INTENT, TurnLabel.OTHER)


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    reason: str
    topic_id: str | None = None
    forced_offer: OfferKind | None = None
    strike: Literal["off_topic", "abuse"] | None = None
    stop: bool = False


def _verbatim_topic(label: "LabelResult", config: "AssistantConfig") -> bool:
    if label.label not in _BUSINESS or label.topic_id is None:
        return False
    return any(t.id == label.topic_id and t.verbatim_answer for t in config.scope.topics)


def _blocked(label: "LabelResult", config: "AssistantConfig") -> str | None:
    for b in config.scope.blocked_topics:
        if label.topic_id == b.id:
            return b.action
    return None


def needs_retrieval(label: "LabelResult", config: "AssistantConfig") -> bool:
    if label.needs_human or _blocked(label, config) or _verbatim_topic(label, config):
        return False
    if label.label in (*_BUSINESS, TurnLabel.DOMAIN_EXPLAINER):
        return True
    return label.label == TurnLabel.OFF_TOPIC and label.confidence < TIE_CONFIDENCE


def decide_route(
    *,
    label: "LabelResult",
    gate: "GateResult | None",
    config: "AssistantConfig",
    state: "ConversationState",
    has_handoff: bool,
) -> RouteDecision:
    handoff_ok = has_handoff and config.handoff.enabled
    topic = label.topic_id
    # TV1 human: a request for a person is answered before the off-topic stop (never refused)
    asked = label.label == TurnLabel.HUMAN_REQUEST or label.needs_human
    if asked and label.label != TurnLabel.ABUSE:
        if handoff_ok:
            return RouteDecision(Route.HANDOFF, "explicit_request", topic, OfferKind.HANDOFF_CARD)
        return RouteDecision(Route.VERBATIM, "contact", topic)
    # /TV1 human
    if state.stopped:
        return RouteDecision(Route.DECLINE, "stopped", topic)
    if label.label == TurnLabel.ABUSE:
        if state.abuse_strikes >= 1 and handoff_ok:
            return RouteDecision(Route.HANDOFF, "abuse", topic, OfferKind.HANDOFF_CARD, "abuse")
        return RouteDecision(Route.ABUSE_WARNING, "abuse", topic, strike="abuse")
    if label.label == TurnLabel.SMALLTALK:
        return RouteDecision(Route.SMALLTALK, "smalltalk", topic)
    action = _blocked(label, config)
    if action is not None:
        if action == "handoff" and handoff_ok:
            return RouteDecision(Route.HANDOFF, "blocked_topic", topic, OfferKind.HANDOFF_CARD)
        return RouteDecision(Route.DECLINE, "blocked_topic", topic)
    if _verbatim_topic(label, config):
        return RouteDecision(Route.VERBATIM, "verbatim", topic)
    passed = gate is not None and gate.passed
    if label.label == TurnLabel.OFF_TOPIC:
        if label.confidence < TIE_CONFIDENCE and passed:
            return RouteDecision(Route.GROUNDED, "tie", topic)
        if config.scope.scope_mode == "open_with_guardrails":
            return RouteDecision(Route.EXPLAINER, "open", topic)
        stop = state.off_topic_strikes + 1 >= config.scope.max_off_topic_strikes
        return RouteDecision(
            Route.DECLINE, "strikes" if stop else "off_topic", topic, strike="off_topic", stop=stop
        )
    if label.label == TurnLabel.DOMAIN_EXPLAINER:
        if passed:
            return RouteDecision(Route.GROUNDED, "docs_match", topic)
        if config.scope.scope_mode == "strict_docs":
            return RouteDecision(Route.NO_ANSWER, "strict_docs", topic)
        return RouteDecision(Route.EXPLAINER, "explainer", topic)
    if gate is None or gate.passed:
        return RouteDecision(Route.GROUNDED, "gate" if gate else "planned", topic)
    return RouteDecision(Route.NO_ANSWER, "gate", topic)
