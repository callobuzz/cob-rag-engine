"""Run comparison: per-metric deltas with significance, per-question improved/regressed."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from callobuzz_rag.evaluation.compare import compare_runs, metric_summary, tag_breakdown


def run(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        kind="golden",
        name=name,
        set_id=None,
        mode="full",
        target="draft",
        config_version=None,
        status="completed",
        triggered_by="manual",
        sample_size=40,
        totals={},
        judge_model="judge-x",
        estimated_cost_usd=0.1,
        actual_cost_usd=0.09,
        actual_cost_known=True,
        created_by="priya@shree-labs.in",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        error_message=None,
        thresholds={},
    )


def samples(
    score: float, passed: bool, n: int = 40, pair_ids: list[uuid.UUID] | None = None
) -> list[SimpleNamespace]:
    ids = pair_ids or [uuid.uuid4() for _ in range(n)]
    return [
        SimpleNamespace(
            golden_pair_id=i,
            question=f"q{k}",
            scores={"answer_correctness": score},
            passed=passed,
            tags={"origin": "human", "difficulty": "easy" if k % 2 else "hard", "topic": "fees"},
        )
        for k, i in enumerate(ids)
    ]


def test_improvement_is_significant_and_questions_improve() -> None:
    ids = [uuid.uuid4() for _ in range(40)]
    a, b = run("baseline"), run("rerank on")
    out = compare_runs(
        [a, b],
        {a.id: samples(0.5, False, pair_ids=ids), b.id: samples(0.8, True, pair_ids=ids)},
        iterations=300,
    )
    (metric,) = [m for m in out.metrics if m.metric == "answer_correctness"]
    assert metric.deltas[0] is None
    assert metric.deltas[1].significant and round(metric.deltas[1].mean, 2) == 0.3
    assert {q.change[1] for q in out.questions} == {"improved"}
    assert out.warnings == []


def test_small_runs_warn() -> None:
    ids = [uuid.uuid4() for _ in range(10)]
    a, b = run("a"), run("b")
    out = compare_runs(
        [a, b],
        {a.id: samples(0.5, True, pair_ids=ids), b.id: samples(0.5, True, pair_ids=ids)},
        iterations=200,
    )
    assert any("fewer than 30" in w for w in out.warnings)
    assert {q.change[1] for q in out.questions} == {"unchanged"}


def test_the_minimum_comes_from_the_caller() -> None:
    ids = [uuid.uuid4() for _ in range(10)]
    a, b = run("a"), run("b")
    out = compare_runs(
        [a, b],
        {a.id: samples(0.5, True, pair_ids=ids), b.id: samples(0.5, True, pair_ids=ids)},
        iterations=100,
        min_samples=5,
    )
    assert out.warnings == []


def test_a_question_only_in_some_runs_is_missing() -> None:
    ids = [uuid.uuid4() for _ in range(3)]
    a, b = run("a"), run("b")
    out = compare_runs(
        [a, b],
        {a.id: samples(0.5, True, pair_ids=ids), b.id: samples(0.5, True, pair_ids=ids[:2])},
        iterations=100,
    )
    assert sorted(q.change[1] for q in out.questions) == ["missing", "unchanged", "unchanged"]


def test_noise_is_not_significant() -> None:
    ids = [uuid.uuid4() for _ in range(40)]
    a, b = run("a"), run("b")
    base = samples(0.7, True, pair_ids=ids)
    other = samples(0.7, True, pair_ids=ids)
    for k, (x, y) in enumerate(zip(base, other, strict=True)):
        x.scores = {"answer_correctness": 0.7 + (0.1 if k % 2 else -0.1)}
        y.scores = {"answer_correctness": 0.7 + (-0.1 if k % 2 else 0.1)}
    out = compare_runs([a, b], {a.id: base, b.id: other}, iterations=300)
    (metric,) = out.metrics
    assert not metric.deltas[1].significant


def test_summary_and_tags() -> None:
    rows = samples(0.8, True, n=12)
    (summary,) = metric_summary(rows, {"answer_correctness": 0.7}, iterations=200, min_samples=30)
    assert summary.n == 12 and summary.pass_rate == 1.0 and summary.small_sample
    difficulty = [t for t in tag_breakdown(rows) if t.tag == "difficulty"]
    assert {t.value for t in difficulty} == {"easy", "hard"}


def test_criterion_thresholds_come_from_the_run() -> None:
    rows = [
        SimpleNamespace(
            golden_pair_id=uuid.uuid4(),
            question="q",
            scores={"criterion:mentions-fee": score},
            passed=None,
            tags={},
        )
        for score in (1.0, 0.0)
    ]
    (summary,) = metric_summary(
        rows, {"criterion:mentions-fee": 0.7}, iterations=100, min_samples=1
    )
    assert summary.threshold == 0.7 and summary.pass_rate == 0.5
