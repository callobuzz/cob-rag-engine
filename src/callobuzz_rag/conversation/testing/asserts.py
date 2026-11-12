"""Judging one test case from the director's outcome. Pure and LLM-free (design section 9)."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

Behaviour = Literal["answer", "decline", "handoff", "lead", "smalltalk", "no_answer"]
Status = Literal["pass", "fail", "skipped"]

_ROUTE_BEHAVIOUR: dict[str, Behaviour] = {
    "grounded": "answer",
    "verbatim": "answer",
    "explainer": "answer",
    "decline": "decline",
    "abuse_warning": "decline",
    "handoff": "handoff",
    "lead": "lead",
    "smalltalk": "smalltalk",
    "no_answer": "no_answer",
}
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_DASHES = re.compile("[" + chr(0x2010) + "-" + chr(0x2015) + "]")  # hyphen..horizontal bar
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{2,3}\b)")
_CURRENCY_SPACE = re.compile("(₹|rs\\.?|inr)\\s+(?=\\d)")


@dataclass(frozen=True)
class CaseSpec:
    expected: Behaviour
    expected_label: str | None
    must_include: tuple[str, ...]
    must_cite: str | None


@dataclass(frozen=True)
class CaseOutcome:
    mode: Literal["fast", "full"]
    label: str | None
    route: str | None
    ui_kind: str | None
    gate_passed: bool | None
    answer_text: str | None
    source_files: tuple[str, ...]
    leads_enabled: bool
    handoff_enabled: bool
    error: str | None = None


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass(frozen=True)
class CaseVerdict:
    passed: bool
    checks: tuple[CheckResult, ...]


def behaviour_of(route: str | None, ui_kind: str | None) -> Behaviour | None:
    """What the visitor experienced: a handoff card or lead form outranks the route."""
    if ui_kind == "handoff_card":
        return "handoff"
    if ui_kind == "lead_form":
        return "lead"
    return _ROUTE_BEHAVIOUR.get(route or "")


def normalise_fact(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).translate(_DEVANAGARI_DIGITS)
    value = _DASHES.sub("-", value.lower())
    value = _THOUSANDS.sub("", value)
    value = _CURRENCY_SPACE.sub(r"\1", value)
    return " ".join(value.split())


def _behaviour_check(spec: CaseSpec, out: CaseOutcome) -> CheckResult:
    actual_route = _ROUTE_BEHAVIOUR.get(out.route or "")
    actual = behaviour_of(out.route, out.ui_kind)
    ok = actual == spec.expected
    if spec.expected == "answer" and actual_route == "answer":
        ok = True  # an offer after a good answer does not make it a non-answer
    if out.mode == "fast" and not ok:
        if spec.expected == "lead" and out.label == "buying_intent" and out.leads_enabled:
            ok = True
        if spec.expected == "handoff" and out.label == "human_request" and out.handoff_enabled:
            ok = True
    detail = f"expected {spec.expected}, got {actual or 'nothing'} (route {out.route})"
    return CheckResult("behaviour", "pass" if ok else "fail", detail)


def _gate_check(spec: CaseSpec, out: CaseOutcome) -> CheckResult | None:
    if spec.expected == "answer" and out.route == "grounded":
        if out.gate_passed is None:
            return CheckResult("gate", "pass", "no threshold configured: search gate not run")
        ok = out.gate_passed is True
        return CheckResult(
            "gate", "pass" if ok else "fail", f"search confidence passed: {out.gate_passed}"
        )
    if spec.expected == "no_answer":
        ok = out.route == "no_answer" or out.gate_passed is False
        return CheckResult(
            "gate", "pass" if ok else "fail", f"search confidence passed: {out.gate_passed}"
        )
    return None


def evaluate_case(spec: CaseSpec, out: CaseOutcome) -> CaseVerdict:
    if out.error:
        return CaseVerdict(False, (CheckResult("error", "fail", out.error[:500]),))
    checks: list[CheckResult] = [_behaviour_check(spec, out)]
    gate = _gate_check(spec, out)
    if gate is not None:
        checks.append(gate)
    if spec.expected_label:
        ok = out.label == spec.expected_label
        checks.append(
            CheckResult(
                "label",
                "pass" if ok else "fail",
                f"expected {spec.expected_label}, got {out.label}",
            )
        )
    if spec.must_include:
        if out.mode == "fast" or out.answer_text is None:
            checks.append(CheckResult("must_include", "skipped", "fast run: no answer generated"))
        else:
            text = normalise_fact(out.answer_text)
            missing = [f for f in spec.must_include if normalise_fact(f) not in text]
            checks.append(
                CheckResult(
                    "must_include",
                    "fail" if missing else "pass",
                    f"missing: {', '.join(missing)}" if missing else "all facts present",
                )
            )
    if spec.must_cite:
        if out.mode == "fast":
            checks.append(CheckResult("must_cite", "skipped", "fast run: no answer generated"))
        else:
            want = spec.must_cite.lower()
            ok = any(want in f.lower() for f in out.source_files)
            checks.append(
                CheckResult(
                    "must_cite",
                    "pass" if ok else "fail",
                    f"sources: {', '.join(out.source_files) or 'none'}",
                )
            )
    return CaseVerdict(all(c.status != "fail" for c in checks), tuple(checks))
