"""Test-case checks are pure functions of the director's outcome (no model calls)."""

import pytest

from callobuzz_rag.conversation.testing.asserts import (
    CaseOutcome,
    CaseSpec,
    behaviour_of,
    evaluate_case,
    normalise_fact,
)

EN_DASH = chr(0x2013)


def spec(expected="answer", label=None, include=(), cite=None) -> CaseSpec:
    return CaseSpec(
        expected=expected, expected_label=label, must_include=tuple(include), must_cite=cite
    )


def outcome(**kw) -> CaseOutcome:
    base = dict(
        mode="full",
        label="business_question",
        route="grounded",
        ui_kind=None,
        gate_passed=True,
        answer_text=f"Fast for 10{EN_DASH}12 hours before a lipid profile [1].",
        source_files=("test-prep-guide.pdf",),
        leads_enabled=True,
        handoff_enabled=True,
    )
    base.update(kw)
    return CaseOutcome(**base)


@pytest.mark.parametrize(
    ("route", "ui", "expected"),
    [
        ("grounded", None, "answer"),
        ("verbatim", None, "answer"),
        ("explainer", None, "answer"),
        ("decline", None, "decline"),
        ("abuse_warning", None, "decline"),
        ("handoff", None, "handoff"),
        ("grounded", "handoff_card", "handoff"),
        ("lead", None, "lead"),
        ("grounded", "lead_form", "lead"),
        ("smalltalk", None, "smalltalk"),
        ("no_answer", None, "no_answer"),
        (None, None, None),
    ],
)
def test_behaviour_of(route, ui, expected) -> None:
    assert behaviour_of(route, ui) == expected


def test_normalise_fact_ignores_formatting() -> None:
    assert normalise_fact("₹1,500") == normalise_fact("₹ 1500") == "₹1500"
    assert normalise_fact("१० घंटे") == "10 घंटे"
    assert normalise_fact(f"10{EN_DASH}12  Hours") == "10-12 hours"


def test_answer_case_passes_with_fact_and_citation() -> None:
    verdict = evaluate_case(spec(include=["10-12 hours"], cite="test-prep-guide.pdf"), outcome())
    assert verdict.passed
    assert {c.name: c.status for c in verdict.checks} == {
        "behaviour": "pass",
        "gate": "pass",
        "must_include": "pass",
        "must_cite": "pass",
    }


def test_answer_case_also_passes_when_a_lead_form_follows() -> None:
    assert evaluate_case(spec(), outcome(ui_kind="lead_form")).passed


def test_missing_fact_fails_with_detail() -> None:
    verdict = evaluate_case(spec(include=["₹1,500"]), outcome())
    assert not verdict.passed
    failed = [c for c in verdict.checks if c.status == "fail"]
    assert failed[0].name == "must_include" and "₹1,500" in failed[0].detail


def test_answer_with_closed_gate_fails() -> None:
    verdict = evaluate_case(spec(), outcome(gate_passed=False, route="no_answer"))
    assert not verdict.passed


def test_grounded_route_with_no_gate_result_passes_when_no_threshold_is_set() -> None:
    verdict = evaluate_case(spec(), outcome(gate_passed=None))
    gate = {c.name: c for c in verdict.checks}["gate"]
    assert verdict.passed and gate.status == "pass"
    assert "no threshold configured" in gate.detail


def test_no_answer_case_needs_a_closed_gate_or_no_answer_route() -> None:
    assert evaluate_case(spec("no_answer"), outcome(route="no_answer", gate_passed=False)).passed
    assert not evaluate_case(spec("no_answer"), outcome()).passed


def test_label_check() -> None:
    assert not evaluate_case(spec(label="buying_intent"), outcome()).passed
    assert evaluate_case(spec(label="business_question"), outcome()).passed


def test_fast_mode_skips_answer_checks() -> None:
    verdict = evaluate_case(
        spec(include=["anything"], cite="x.pdf"),
        outcome(mode="fast", answer_text=None, source_files=()),
    )
    assert verdict.passed
    assert {c.name: c.status for c in verdict.checks}["must_include"] == "skipped"


def test_fast_mode_lead_from_buying_intent() -> None:
    ok = outcome(mode="fast", label="buying_intent", route="grounded", answer_text=None)
    assert evaluate_case(spec("lead"), ok).passed
    off = outcome(mode="fast", label="buying_intent", leads_enabled=False)
    assert not evaluate_case(spec("lead"), off).passed


def test_fast_mode_handoff_from_human_request() -> None:
    ok = outcome(mode="fast", label="human_request", route="grounded", answer_text=None)
    assert evaluate_case(spec("handoff"), ok).passed


def test_error_fails_everything() -> None:
    verdict = evaluate_case(spec(), outcome(error="LLM timed out"))
    assert not verdict.passed
    assert verdict.checks[0].name == "error"
