"""TV1: offer a person after a miss (`handoff.triggers.offer_after_misses`, default 1)."""

import pytest
from pydantic import ValidationError

from callobuzz_rag.conversation.handoff.triggers import (
    REASON_LABELS,
    HandoffReason,
    handoff_reason,
)
from callobuzz_rag.conversation.schema import AssistantConfig, load_config
from callobuzz_rag.conversation.signals import TurnSignals
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.types import OfficeState, Route, TurnLabel

CFG = AssistantConfig()


def _signals(**kw: object) -> TurnSignals:
    base: dict[str, object] = {
        "label": TurnLabel.BUSINESS_QUESTION,
        "text": "What is the price of the zeta scan?",
        "office": OfficeState.OPEN,
    }
    base.update(kw)
    return TurnSignals.for_test(**base)


def test_first_miss_offers_a_person_by_default() -> None:
    s = _signals(route=Route.NO_ANSWER, gate_passed=False, no_answer=True)
    assert handoff_reason(CFG, s, ConversationState()) is HandoffReason.MISSES


@pytest.mark.parametrize(
    "n,misses_before,fires", [(0, 9, False), (2, 0, False), (2, 1, True), (5, 4, True)]
)
def test_offer_after_n_misses(n: int, misses_before: int, fires: bool) -> None:
    cfg = AssistantConfig.model_validate({"handoff": {"triggers": {"offer_after_misses": n}}})
    s = _signals(route=Route.DECLINE, label=TurnLabel.OFF_TOPIC, gate_passed=None)
    got = handoff_reason(cfg, s, ConversationState(miss_count=misses_before))
    assert (got is HandoffReason.MISSES) is fires


def test_misses_are_not_repeated_after_a_card() -> None:
    s = _signals(route=Route.NO_ANSWER, no_answer=True, gate_passed=None)
    state = ConversationState(handoff_offered=True, miss_count=3)
    assert handoff_reason(CFG, s, state) is None


def test_a_good_turn_after_an_old_miss_offers_nothing() -> None:
    s = _signals(route=Route.GROUNDED, gate_passed=True)
    assert handoff_reason(CFG, s, ConversationState(miss_count=4)) is None


def test_what_counts_as_a_miss() -> None:
    st = ConversationState()
    st.after_turn(label=TurnLabel.BUSINESS_QUESTION, route=Route.GROUNDED, gate_passed=True)
    st.after_turn(label=TurnLabel.SMALLTALK, route=Route.SMALLTALK, gate_passed=None)
    assert st.miss_count == 0
    st.after_turn(label=TurnLabel.OFF_TOPIC, route=Route.DECLINE, gate_passed=None)  # +1
    st.after_turn(label=TurnLabel.BUSINESS_QUESTION, route=Route.NO_ANSWER, gate_passed=False)  # +1
    assert st.miss_count == 2


def test_a_decline_of_a_stopped_conversation_is_not_another_miss() -> None:
    st = ConversationState()
    st.after_turn(
        label=TurnLabel.OFF_TOPIC, route=Route.DECLINE, gate_passed=None, was_stopped=True
    )
    assert st.miss_count == 0


def test_offer_after_misses_bounds() -> None:
    with pytest.raises(ValidationError):
        AssistantConfig.model_validate({"handoff": {"triggers": {"offer_after_misses": 6}}})
    with pytest.raises(ValidationError):
        AssistantConfig.model_validate({"handoff": {"triggers": {"offer_after_misses": -1}}})
    assert AssistantConfig().handoff.triggers.offer_after_misses == 1


def test_stored_config_without_the_field_reads_the_default() -> None:
    raw = AssistantConfig().model_dump(mode="json")
    del raw["handoff"]["triggers"]["offer_after_misses"]
    assert load_config(raw).handoff.triggers.offer_after_misses == 1


def test_new_reasons_have_owner_labels() -> None:
    assert REASON_LABELS[HandoffReason.MISSES] == "Could not answer"
    assert REASON_LABELS[HandoffReason.LOOP] == "The assistant was repeating itself"


def test_loop_reason_fires_when_the_reply_repeated_and_is_repeatable() -> None:
    state = ConversationState(repeat_count=2, handoff_offered=True)
    got = handoff_reason(CFG, _signals(), state)
    assert got is HandoffReason.LOOP


@pytest.mark.parametrize("stopped,fires", [(False, True), (True, False)])
def test_the_stop_reply_of_a_stopped_conversation_does_not_fire_misses(
    stopped: bool, fires: bool
) -> None:
    cfg = AssistantConfig.model_validate({"handoff": {"triggers": {"offer_after_misses": 4}}})
    s = _signals(route=Route.DECLINE, label=TurnLabel.OFF_TOPIC, gate_passed=None, stopped=stopped)
    state = ConversationState(miss_count=3, stopped=stopped)
    assert (handoff_reason(cfg, s, state) is HandoffReason.MISSES) is fires
