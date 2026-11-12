"""Run summaries with confidence intervals, tag breakdowns and run-to-run comparison (C7)."""

from collections import defaultdict
from typing import Any, Literal, cast

from callobuzz_rag.api.schemas.evals import (
    ComparisonOut,
    DeltaOut,
    MetricComparison,
    MetricSummary,
    QuestionDelta,
    RunSummary,
    TagBreakdown,
)
from callobuzz_rag.evaluation.metrics import CRITERION_PREFIX, DEFAULT_THRESHOLDS
from callobuzz_rag.evaluation.stats import bootstrap_ci, paired_delta

MIN_SAMPLES = 30
# A question whose pass/fail did not change still counts as moved at this mean-score change.
QUESTION_MOVE = 0.1
Change = Literal["baseline", "improved", "regressed", "unchanged", "missing"]


def _metrics(samples: list[Any]) -> list[str]:
    names: list[str] = []
    for s in samples:
        for k, v in (s.scores or {}).items():
            if v is not None and k not in names:
                names.append(k)
    return names


def _values(samples: list[Any], metric: str) -> list[float]:
    return [float(s.scores[metric]) for s in samples if (s.scores or {}).get(metric) is not None]


def metric_summary(
    samples: list[Any],
    thresholds: dict[str, float],
    *,
    iterations: int,
    min_samples: int = MIN_SAMPLES,
) -> list[MetricSummary]:
    """Mean, 95 % bootstrap interval, pass rate and a small-sample flag per scored metric.

    `thresholds` are the run's: metric overrides plus one `criterion:<name>` entry per owner
    criterion."""
    t = DEFAULT_THRESHOLDS.with_overrides(thresholds)
    out: list[MetricSummary] = []
    for metric in _metrics(samples):
        values = _values(samples, metric)
        stat = bootstrap_ci(values, iterations=iterations)
        threshold = thresholds.get(metric) if metric.startswith(CRITERION_PREFIX) else t.get(metric)
        pass_rate = (
            sum(1 for v in values if threshold is not None and v >= threshold) / len(values)
            if values
            else None
        )
        out.append(
            MetricSummary(
                metric=metric,
                n=stat.n,
                mean=stat.mean,
                ci_low=stat.low,
                ci_high=stat.high,
                threshold=threshold,
                pass_rate=pass_rate,
                small_sample=stat.n < min_samples,
            )
        )
    return out


def tag_breakdown(samples: list[Any]) -> list[TagBreakdown]:
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for s in samples:
        for tag in ("origin", "difficulty", "topic"):
            value = (s.tags or {}).get(tag)
            if value:
                groups[(tag, str(value))].append(s)
    out: list[TagBreakdown] = []
    for (tag, value), rows in sorted(groups.items()):
        means: dict[str, float | None] = {}
        for metric in _metrics(rows):
            values = _values(rows, metric)
            means[metric] = sum(values) / len(values) if values else None
        judged = [r for r in rows if r.passed is not None]
        out.append(
            TagBreakdown(
                tag=cast("Literal['origin', 'difficulty', 'topic']", tag),
                value=value,
                n=len(rows),
                means=means,
                pass_rate=sum(1 for r in judged if r.passed) / len(judged) if judged else None,
            )
        )
    return out


def _mean(scores: dict[str, Any]) -> float | None:
    values = [float(v) for v in scores.values() if v is not None]
    return sum(values) / len(values) if values else None


def _moved(before: float | None, after: float | None) -> str | None:
    """'improved' / 'regressed' when the mean score moved by QUESTION_MOVE or more."""
    if before is None or after is None or abs(after - before) < QUESTION_MOVE:
        return None
    return "improved" if after > before else "regressed"


def _empty_summary(metric: str) -> MetricSummary:
    return MetricSummary(
        metric=metric,
        n=0,
        mean=None,
        ci_low=None,
        ci_high=None,
        threshold=None,
        pass_rate=None,
        small_sample=True,
    )


def compare_runs(
    runs: list[Any],
    samples: dict[Any, list[Any]],
    *,
    iterations: int,
    min_samples: int = MIN_SAMPLES,
) -> ComparisonOut:
    """Per-metric means with intervals, a paired delta against the first run (significant when
    its interval excludes 0) and improved / regressed / unchanged per question."""
    warnings: list[str] = []
    per_run = {
        r.id: {s.golden_pair_id: s for s in samples.get(r.id, []) if s.golden_pair_id} for r in runs
    }
    if any(len(v) < min_samples for v in per_run.values()):
        warnings.append(
            f"Some runs have fewer than {min_samples} questions: differences may be noise."
        )
    metric_names: list[str] = []
    for r in runs:
        for m in _metrics(samples.get(r.id, [])):
            if m not in metric_names:
                metric_names.append(m)
    # Each run's summaries are computed once, not once per metric.
    summaries = {
        r.id: {
            s.metric: s
            for s in metric_summary(
                samples.get(r.id, []),
                r.thresholds or {},
                iterations=iterations,
                min_samples=min_samples,
            )
        }
        for r in runs
    }
    base = runs[0]
    metrics: list[MetricComparison] = []
    for metric in metric_names:
        base_values = {
            k: float(s.scores[metric])
            for k, s in per_run[base.id].items()
            if (s.scores or {}).get(metric) is not None
        }
        deltas: list[DeltaOut | None] = [None]
        for r in runs[1:]:
            other = {
                k: float(s.scores[metric])
                for k, s in per_run[r.id].items()
                if (s.scores or {}).get(metric) is not None
            }
            d = paired_delta(base_values, other, iterations=iterations)
            deltas.append(
                DeltaOut(
                    n=d.n,
                    mean=d.mean,
                    ci_low=d.low,
                    ci_high=d.high,
                    significant=d.significant,
                )
            )
        metrics.append(
            MetricComparison(
                metric=metric,
                per_run=[summaries[r.id].get(metric) or _empty_summary(metric) for r in runs],
                deltas=deltas,
            )
        )
    questions: list[QuestionDelta] = []
    for pair_id, base_sample in per_run[base.id].items():
        cells = [per_run[r.id].get(pair_id) for r in runs]
        passed = [c.passed if c is not None else None for c in cells]
        means = [_mean(c.scores or {}) if c is not None else None for c in cells]
        change: list[Change] = ["baseline"]
        for i in range(1, len(runs)):
            if cells[i] is None:
                change.append("missing")
            elif passed[i] is True and passed[0] is not True:
                change.append("improved")
            elif passed[i] is False and passed[0] is True:
                change.append("regressed")
            elif _moved(means[0], means[i]) is not None:
                change.append(cast("Change", _moved(means[0], means[i])))
            else:
                change.append("unchanged")
        questions.append(
            QuestionDelta(
                golden_pair_id=pair_id,
                question=base_sample.question,
                per_run_passed=passed,
                per_run_mean=means,
                change=change,
            )
        )
    return ComparisonOut(
        runs=[RunSummary.model_validate(r, from_attributes=True) for r in runs],
        metrics=metrics,
        questions=questions,
        warnings=warnings,
    )
