"""When the assistant offers a person, and why (design 4.8, research G).

Order: explicit request, blocked topic (action handoff), abuse after the warning, a repeated
reply (TV1 loop), owner keywords, frustration, a miss (TV1), low confidence in a row, N unresolved
turns, out of hours. After a card
was offered in this conversation only the first three offer it again.

The director asks the registry before `state.after_turn` runs, so the counters here do not
yet include the current turn: they are projected with the same rule C1 applies afterwards.
"""

import dataclasses
import unicodedata
from enum import StrEnum

from callobuzz_rag.conversation.loop_guard import loop_detected
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.signals import TurnSignals
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.types import OfficeState, Route, TurnLabel
from callobuzz_rag.text.tokenize import word_pattern


class HandoffReason(StrEnum):
    EXPLICIT_REQUEST = "explicit_request"
    FRUSTRATION = "frustration"
    UNRESOLVED = "unresolved"
    LOW_CONFIDENCE = "low_confidence"
    BLOCKED_TOPIC = "blocked_topic"
    KEYWORD = "keyword"
    OUT_OF_HOURS = "out_of_hours"
    ABUSE = "abuse"
    NO_ANSWER = "no_answer"
    MISSES = "misses"  # TV1 human
    LOOP = "loop"  # TV1 human


REASON_LABELS: dict[HandoffReason, str] = {
    HandoffReason.EXPLICIT_REQUEST: "Asked for a person",
    HandoffReason.FRUSTRATION: "Seemed frustrated",
    HandoffReason.UNRESOLVED: "Question not resolved after several messages",
    HandoffReason.LOW_CONFIDENCE: "Two answers in a row without documented information",
    HandoffReason.BLOCKED_TOPIC: "Topic the assistant must not handle",
    HandoffReason.KEYWORD: "Mentioned a keyword you watch (refund, cancel, complaint…)",
    HandoffReason.OUT_OF_HOURS: "Outside office hours",
    HandoffReason.ABUSE: "Abusive messages after a warning",
    HandoffReason.NO_ANSWER: "No documented answer",
    HandoffReason.MISSES: "Could not answer",  # TV1 human
    HandoffReason.LOOP: "The assistant was repeating itself",  # TV1 human
}

_REPEATABLE = {
    HandoffReason.EXPLICIT_REQUEST,
    HandoffReason.BLOCKED_TOPIC,
    HandoffReason.ABUSE,
    HandoffReason.LOOP,  # TV1 human: a repeated reply is always answered with a person
}


def reason_label(reason: str) -> str:
    try:
        return REASON_LABELS[HandoffReason(reason)]
    except ValueError:
        return reason


def normalise(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _is_latin(word: str) -> bool:
    return all(ord(ch) < 0x0250 for ch in word)


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    """Keywords found in `text`. Latin keywords on word boundaries, others as substrings."""
    haystack = normalise(text)
    hits: list[str] = []
    for keyword in keywords:
        needle = normalise(keyword)
        if not needle:
            continue
        if _is_latin(needle):
            if word_pattern(needle).search(haystack):
                hits.append(keyword)
        elif needle in haystack:
            hits.append(keyword)
    return hits


def forced_reason(signals: TurnSignals) -> HandoffReason:
    """The reason for a card the director forced (it passes the literal "forced")."""
    if signals.label is TurnLabel.ABUSE:
        return HandoffReason.ABUSE
    if signals.blocked_action == "handoff":
        return HandoffReason.BLOCKED_TOPIC
    if signals.label is TurnLabel.HUMAN_REQUEST or signals.needs_human:
        return HandoffReason.EXPLICIT_REQUEST
    return HandoffReason.NO_ANSWER


def _is_miss(signals: TurnSignals) -> bool:  # TV1 human
    return (
        signals.route is Route.NO_ANSWER
        or signals.gate_passed is False
        or (signals.route is Route.DECLINE and not signals.stopped)
    )


def _projected(state: ConversationState, signals: TurnSignals) -> ConversationState:
    projected = dataclasses.replace(state)
    projected.after_turn(
        label=signals.label,
        route=signals.route,
        gate_passed=signals.gate_passed,
        was_stopped=signals.stopped,
    )
    return projected


def _candidates(
    config: AssistantConfig, signals: TurnSignals, state: ConversationState
) -> list[HandoffReason]:
    t = config.handoff.triggers
    after = _projected(state, signals)
    found: list[HandoffReason] = []
    if t.explicit_request and (signals.label is TurnLabel.HUMAN_REQUEST or signals.needs_human):
        found.append(HandoffReason.EXPLICIT_REQUEST)
    if t.blocked_topics and signals.blocked_topic_id and signals.blocked_action == "handoff":
        found.append(HandoffReason.BLOCKED_TOPIC)
    if signals.label is TurnLabel.ABUSE and signals.route is Route.HANDOFF:
        found.append(HandoffReason.ABUSE)
    if loop_detected(state):  # TV1 human: the same reply twice in a row
        found.append(HandoffReason.LOOP)
    if t.keywords and keyword_hits(signals.text, t.keywords):
        found.append(HandoffReason.KEYWORD)
    if t.frustration and signals.frustrated:
        found.append(HandoffReason.FRUSTRATION)
    # TV1 human: this turn is itself a miss and the owner's count is reached
    if t.offer_after_misses and _is_miss(signals) and after.miss_count >= t.offer_after_misses:
        found.append(HandoffReason.MISSES)
    if t.low_confidence_streak and after.low_conf_streak >= t.low_confidence_streak:
        found.append(HandoffReason.LOW_CONFIDENCE)
    if t.unresolved_turns and after.unresolved_turns >= t.unresolved_turns:
        found.append(HandoffReason.UNRESOLVED)
    if (
        t.out_of_hours
        and signals.office is OfficeState.CLOSED
        and (signals.gate_passed is False or signals.label is TurnLabel.BUYING_INTENT)
    ):
        found.append(HandoffReason.OUT_OF_HOURS)
    return found


def handoff_reason(
    config: AssistantConfig, signals: TurnSignals, state: ConversationState
) -> HandoffReason | None:
    """The first trigger that fires, or None. Disabled handoff never fires."""
    if not config.handoff.enabled:
        return None
    for reason in _candidates(config, signals, state):
        if state.handoff_offered and reason not in _REPEATABLE:
            continue
        return reason
    return None
