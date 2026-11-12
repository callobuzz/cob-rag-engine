"""C7 data model: one quality system (golden sets, pairs, runs, samples)."""

from sqlalchemy import CheckConstraint

from callobuzz_rag.db.models import (
    AssistantReviewAction,
    EvalCriterion,
    EvalRun,
    EvalRunSample,
    EvalSettings,
    GoldenPair,
    GoldenSet,
)
from callobuzz_rag.db.models.golden_set import DIFFICULTIES, EXPECTED_BEHAVIOURS, ORIGINS


def _fk(model, column: str) -> tuple[str, str]:
    (fk,) = model.__table__.c[column].foreign_keys
    return fk.target_fullname, fk.ondelete


def _checks(model) -> str:
    return " ".join(
        str(c.sqltext) for c in model.__table__.constraints if isinstance(c, CheckConstraint)
    )


def test_cascades() -> None:
    assert _fk(GoldenSet, "corpus_id") == ("corpora.id", "CASCADE")
    assert _fk(GoldenPair, "set_id") == ("golden_sets.id", "CASCADE")
    assert _fk(EvalRun, "set_id") == ("golden_sets.id", "SET NULL")
    assert _fk(EvalRunSample, "run_id") == ("eval_run.id", "CASCADE")
    assert _fk(EvalRunSample, "golden_pair_id") == ("golden_pair.id", "SET NULL")
    assert _fk(EvalRunSample, "query_log_id") == ("query_log.id", "SET NULL")
    assert _fk(EvalCriterion, "corpus_id") == ("corpora.id", "CASCADE")
    assert _fk(EvalSettings, "corpus_id") == ("corpora.id", "CASCADE")
    assert _fk(AssistantReviewAction, "corpus_id") == ("corpora.id", "CASCADE")


def test_pair_needs_an_answer_or_a_behaviour() -> None:
    checks = _checks(GoldenPair)
    assert "expected_answer IS NOT NULL OR expected_behaviour IS NOT NULL" in checks
    assert GoldenPair.__table__.c["expected_answer"].nullable
    for value in EXPECTED_BEHAVIOURS + ORIGINS + DIFFICULTIES:
        assert f"'{value}'" in checks


def test_vocabularies() -> None:
    assert EXPECTED_BEHAVIOURS == ("answer", "decline", "handoff", "lead", "smalltalk", "no_answer")
    assert ORIGINS == ("human", "ai_generated", "from_feedback")
    assert DIFFICULTIES == ("easy", "medium", "hard")
    assert "'traffic'" in _checks(EvalRun) and "'golden'" in _checks(EvalRun)


def test_one_default_set_per_corpus() -> None:
    (index,) = [i for i in GoldenSet.__table__.indexes if i.name == "uq_golden_sets_default"]
    assert index.unique and "is_default" in str(index.dialect_options["postgresql"]["where"])
