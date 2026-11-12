"""Bootstrap confidence intervals: the significance guard for runs and comparisons (C7)."""

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class MetricStat:
    n: int
    mean: float | None
    low: float | None
    high: float | None


@dataclass(frozen=True)
class DeltaStat:
    n: int
    mean: float | None
    low: float | None
    high: float | None
    significant: bool


def _percentile(sorted_values: list[float], q: float) -> float:
    index = min(len(sorted_values) - 1, max(0, round(q * (len(sorted_values) - 1))))
    return sorted_values[index]


def bootstrap_ci(
    values: list[float], *, iterations: int = 1000, seed: int = 7, alpha: float = 0.05
) -> MetricStat:
    """Mean with a percentile-bootstrap interval; a fixed seed keeps a run's numbers stable."""
    n = len(values)
    if n == 0:
        return MetricStat(0, None, None, None)
    mean = sum(values) / n
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(iterations))
    return MetricStat(n, mean, _percentile(means, alpha / 2), _percentile(means, 1 - alpha / 2))


def paired_delta(
    base: dict[str, float], other: dict[str, float], *, iterations: int = 1000, seed: int = 7
) -> DeltaStat:
    """other - base over the questions both scored; significant when the interval excludes 0."""
    keys = sorted(base.keys() & other.keys())
    diffs = [other[k] - base[k] for k in keys]
    stat = bootstrap_ci(diffs, iterations=iterations, seed=seed)
    if stat.n == 0 or stat.low is None or stat.high is None:
        return DeltaStat(0, None, None, None, False)
    significant = stat.low > 0 or stat.high < 0
    return DeltaStat(stat.n, stat.mean, stat.low, stat.high, significant)
