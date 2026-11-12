"""The guided-setup interview: 12 questions as data (questions.json), shown to the owner one
at a time. Two conditional rules: the sensitive-profile question is skipped when the chosen
template already fixes a profile, and questions with `prefill_from` start from what the
website proposal found (`proposal["services"]`, `proposal["hours"]`).
"""

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any, Literal, TypedDict

from pydantic import ValidationError

from callobuzz_rag.core.exceptions import RAGKernelError

Kind = Literal["single", "multi", "text", "list", "hours"]
_TEXT_MAX = 200
_NAME_MAX = 40
_HOURS_MAX = 21


class InterviewAnswerError(RAGKernelError):
    """An answer that does not fit its question."""

    def __init__(self, question_id: str, reason: str) -> None:
        super().__init__(f"{question_id}: {reason}")
        self.question_id = question_id
        self.reason = reason


@dataclass(frozen=True)
class Option:
    value: str
    label: str


@dataclass(frozen=True)
class Question:
    id: str
    kind: Kind
    title: str
    help: str
    options: tuple[Option, ...]
    other: bool
    required: bool
    skip_if_template_profile: bool = False
    prefill_from: str | None = None
    max_items: int | None = None


class QuestionView(TypedDict):
    question: Question
    prefill: Any
    skipped_reason: str | None


@cache
def load_questions() -> tuple[Question, ...]:
    raw = json.loads(
        resources.files("callobuzz_rag.conversation.setup")
        .joinpath("questions.json")
        .read_text("utf-8")
    )
    return tuple(
        Question(
            id=q["id"],
            kind=q["kind"],
            title=q["title"],
            help=q.get("help", ""),
            options=tuple(Option(o["value"], o["label"]) for o in q.get("options", [])),
            other=bool(q.get("other", False)),
            required=bool(q.get("required", False)),
            skip_if_template_profile=bool(q.get("skip_if_template_profile", False)),
            prefill_from=q.get("prefill_from"),
            max_items=q.get("max_items"),
        )
        for q in raw["questions"]
    )


def _template_profile(business_type: str) -> str:
    from callobuzz_rag.conversation.templates import load_template

    return str(load_template(business_type).config.scope.sensitive_profile)


def visible_questions(business_type: str, proposal: dict[str, Any] | None) -> list[QuestionView]:
    """Every question, with its prefill and why it is skipped (None = ask it)."""
    profile = _template_profile(business_type)
    views: list[QuestionView] = []
    for q in load_questions():
        skipped = "template" if q.skip_if_template_profile and profile != "none" else None
        prefill = (proposal or {}).get(q.prefill_from) if q.prefill_from else None
        views.append({"question": q, "prefill": prefill or None, "skipped_reason": skipped})
    return views


def _check_choice(q: Question, value: Any) -> Any:
    allowed = {o.value for o in q.options}
    if isinstance(value, dict):
        other = value.get("other")
        if (
            not q.other
            or set(value) != {"other"}
            or not isinstance(other, str)
            or not other.strip()
        ):
            raise InterviewAnswerError(q.id, "invalid_other")
        if len(other) > _TEXT_MAX:
            raise InterviewAnswerError(q.id, "too_long")
        return {"other": other.strip()}
    if not isinstance(value, str) or value not in allowed:
        raise InterviewAnswerError(q.id, "unknown_option")
    return value


def _check_hours(q: Question, value: Any) -> list[dict[str, str]]:
    """Each row is a C2 `DayHours`: overnight (18:00 to 02:00) is fine, equal times are not."""
    from callobuzz_rag.conversation.schema import DayHours

    if not isinstance(value, list) or len(value) > _HOURS_MAX:
        raise InterviewAnswerError(q.id, "invalid_hours")
    out: list[dict[str, str]] = []
    for item in value:
        try:
            row = DayHours.model_validate(item)
        except ValidationError as exc:
            raise InterviewAnswerError(q.id, "invalid_hours") from exc
        out.append({"day": row.day, "open": row.open, "close": row.close})
    return out


def _check_multi(q: Question, value: Any) -> list[Any]:
    if not isinstance(value, list) or (q.max_items and len(value) > q.max_items):
        raise InterviewAnswerError(q.id, "too_many")
    checked = [_check_choice(q, v) for v in value]
    others = [v for v in checked if isinstance(v, dict)]
    if len(others) > 1:
        raise InterviewAnswerError(q.id, "invalid_other")
    plain = [v for v in checked if not isinstance(v, dict)]
    if len(set(plain)) != len(plain):
        raise InterviewAnswerError(q.id, "too_many")
    return checked


def validate_answers(
    answers: dict[str, Any], business_type: str, *, partial: bool = False
) -> dict[str, Any]:
    """The answers, cleaned. Raises InterviewAnswerError for the first bad one.

    `partial` saves progress: a required question may still be unanswered (generation checks
    the whole set), but every answer that is given must be valid."""
    known = {q.id: q for q in load_questions()}
    unknown = set(answers) - set(known)
    if unknown:
        raise InterviewAnswerError(sorted(unknown)[0], "unknown_question")
    skipped = {
        v["question"].id for v in visible_questions(business_type, None) if v["skipped_reason"]
    }
    clean: dict[str, Any] = {}
    for qid, q in known.items():
        value = answers.get(qid)
        if value in (None, "", []):
            if q.required and qid not in skipped and not partial:
                raise InterviewAnswerError(qid, "required")
            continue
        if q.kind == "single":
            clean[qid] = _check_choice(q, value)
        elif q.kind == "multi":
            clean[qid] = _check_multi(q, value)
        elif q.kind == "list":
            if not isinstance(value, list) or (q.max_items and len(value) > q.max_items):
                raise InterviewAnswerError(qid, "too_many")
            items = [str(v).strip() for v in value if str(v).strip()]
            if any(len(v) > _TEXT_MAX for v in items):
                raise InterviewAnswerError(qid, "too_long")
            clean[qid] = items
        elif q.kind == "text":
            text = str(value).strip()
            if not 1 <= len(text) <= _NAME_MAX:
                raise InterviewAnswerError(qid, "too_long")
            clean[qid] = text
        else:
            clean[qid] = _check_hours(q, value)
    return clean
