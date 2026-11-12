"""Guided setup (C8): the session model, settings and the interview."""

import pytest

from callobuzz_rag.conversation.setup.interview import (
    InterviewAnswerError,
    load_questions,
    validate_answers,
    visible_questions,
)
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.assistant_setup import SETUP_STATUSES, AssistantSetupSession

VALID = {
    "audience": "patients",
    "tone": "warm",
    "languages": ["en", "hi-Latn"],
    "never_do": ["diagnose", "quote_unlisted_prices"],
    "when_unsure": "whatsapp",
    "top_services": ["Blood tests", "Home sample collection", "Health packages"],
    "lead_followup": "same_day",
    "exact_answers": ["prices", "timings"],
    "hours": [{"day": "mon", "open": "07:00", "close": "20:00"}],
    "sensitive_profile": "none",
    "assistant_name": "Asha",
    "greeting_style": "welcome_with_services",
}


def test_session_model_columns() -> None:
    cols = set(AssistantSetupSession.__table__.columns.keys())
    assert {
        "id",
        "corpus_id",
        "status",
        "business_type",
        "website_url",
        "crawl_job_id",
        "proposal",
        "profile",
        "answers",
        "generated_config",
        "provenance",
        "golden_set_id",
        "eval_run_id",
        "created_by",
        "created_at",
        "updated_at",
    } <= cols
    table = AssistantSetupSession.__table__

    def ondelete(column: str) -> str | None:
        return next(iter(table.c[column].foreign_keys)).ondelete

    assert ondelete("corpus_id") == "CASCADE"
    assert ondelete("golden_set_id") == "SET NULL"
    assert ondelete("eval_run_id") == "SET NULL"


def test_python_side_defaults() -> None:
    table = AssistantSetupSession.__table__
    assert table.c.status.default.arg == "started"
    assert table.c.answers.default.arg(None) == {}
    assert table.c.provenance.default.arg(None) == {}


def test_statuses_in_order() -> None:
    assert SETUP_STATUSES == (
        "started",
        "proposed",
        "interviewing",
        "generated",
        "tested",
        "applied",
        "abandoned",
    )


def test_setup_settings_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.setup_max_pages == 15
    assert s.setup_max_chars == 60000


@pytest.mark.parametrize("value", [0, 51])
def test_setup_max_pages_bounds(value: int) -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, setup_max_pages=value)


def test_twelve_questions_mostly_multiple_choice() -> None:
    questions = load_questions()
    assert len(questions) == 12
    assert len({q.id for q in questions}) == 12
    assert sum(q.kind in ("single", "multi") for q in questions) >= 8


def test_sensitive_question_skipped_when_template_fixes_profile() -> None:
    views = {v["question"].id: v for v in visible_questions("clinic", None)}
    assert views["sensitive_profile"]["skipped_reason"] == "template"
    views = {v["question"].id: v for v in visible_questions("store", None)}
    assert views["sensitive_profile"]["skipped_reason"] is None


def test_hours_prefilled_from_proposal() -> None:
    proposal = {
        "hours": [{"day": "sat", "open": "08:00", "close": "14:00"}],
        "services": ["CBC test"],
    }
    views = {v["question"].id: v for v in visible_questions("clinic", proposal)}
    assert views["hours"]["prefill"] == proposal["hours"]
    assert views["top_services"]["prefill"] == ["CBC test"]


def test_valid_answers_pass_and_unknown_option_fails() -> None:
    assert validate_answers(VALID, "clinic")["tone"] == "warm"
    with pytest.raises(InterviewAnswerError) as err:
        validate_answers({**VALID, "tone": "sarcastic"}, "clinic")
    assert err.value.question_id == "tone"


def test_other_text_allowed_where_enabled() -> None:
    other = {"other": "Parents of class 8-12 students"}
    assert validate_answers({**VALID, "audience": other}, "coaching")["audience"] == other
    with pytest.raises(InterviewAnswerError):
        validate_answers({**VALID, "tone": {"other": "sarcastic"}}, "clinic")


def test_required_missing_fails() -> None:
    answers = dict(VALID)
    del answers["when_unsure"]
    with pytest.raises(InterviewAnswerError) as err:
        validate_answers(answers, "store")
    assert err.value.reason == "required"


def test_list_limits() -> None:
    with pytest.raises(InterviewAnswerError):
        validate_answers({**VALID, "top_services": [f"s{i}" for i in range(11)]}, "store")


def test_overnight_hours_accepted_and_equal_times_refused() -> None:
    night = [{"day": "fri", "open": "18:00", "close": "02:00"}]
    assert validate_answers({**VALID, "hours": night}, "restaurant")["hours"] == night
    same = [{"day": "fri", "open": "09:00", "close": "09:00"}]
    with pytest.raises(InterviewAnswerError) as err:
        validate_answers({**VALID, "hours": same}, "store")
    assert err.value.reason == "invalid_hours"
    bad = [{"day": "fri", "open": "9am", "close": "5pm"}]
    with pytest.raises(InterviewAnswerError):
        validate_answers({**VALID, "hours": bad}, "store")


def test_two_hours_ranges_for_one_day() -> None:
    split = [
        {"day": "mon", "open": "09:00", "close": "13:00"},
        {"day": "mon", "open": "16:00", "close": "20:00"},
    ]
    assert validate_answers({**VALID, "hours": split}, "store")["hours"] == split


def test_at_most_one_other_and_no_duplicate_options() -> None:
    with pytest.raises(InterviewAnswerError) as err:
        validate_answers({**VALID, "never_do": [{"other": "a"}, {"other": "b"}]}, "store")
    assert err.value.reason == "invalid_other"
    with pytest.raises(InterviewAnswerError) as err:
        validate_answers({**VALID, "never_do": ["diagnose", "diagnose"]}, "store")
    assert err.value.reason == "too_many"


def test_partial_saves_progress_but_still_validates_what_is_given() -> None:
    assert validate_answers({"tone": "warm"}, "store", partial=True) == {"tone": "warm"}
    assert validate_answers({}, "store", partial=True) == {}
    with pytest.raises(InterviewAnswerError) as err:
        validate_answers({"tone": "sarcastic"}, "store", partial=True)
    assert err.value.reason == "unknown_option"
    with pytest.raises(InterviewAnswerError) as err:
        validate_answers({"tone": "warm"}, "store")
    assert err.value.reason == "required"
