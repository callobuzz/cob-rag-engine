"""Bootstrap confidence intervals and paired deltas (the significance guard)."""

from callobuzz_rag.evaluation.stats import bootstrap_ci, paired_delta


def test_ci_brackets_the_mean_and_is_reproducible() -> None:
    values = [0.9, 0.8, 0.85, 0.7, 0.95, 0.6, 0.88, 0.92, 0.75, 0.81]
    a = bootstrap_ci(values, iterations=500)
    b = bootstrap_ci(values, iterations=500)
    assert a == b
    assert a.n == 10 and a.low <= a.mean <= a.high
    assert round(a.mean, 3) == 0.816


def test_constant_values_have_a_zero_width_interval() -> None:
    stat = bootstrap_ci([1.0] * 12)
    assert stat.low == stat.high == stat.mean == 1.0


def test_empty_is_none() -> None:
    stat = bootstrap_ci([])
    assert stat.n == 0 and stat.mean is None and stat.low is None


def test_clear_improvement_is_significant() -> None:
    base = {f"q{i}": 0.5 for i in range(40)}
    better = {f"q{i}": 0.8 for i in range(40)}
    delta = paired_delta(base, better)
    assert delta.n == 40 and round(delta.mean, 2) == 0.3 and delta.significant


def test_noise_is_not_significant() -> None:
    base = {f"q{i}": 0.7 + (0.1 if i % 2 else -0.1) for i in range(20)}
    other = {f"q{i}": 0.7 + (-0.1 if i % 2 else 0.1) for i in range(20)}
    delta = paired_delta(base, other)
    assert delta.low <= 0 <= delta.high and not delta.significant


def test_only_shared_questions_are_paired() -> None:
    delta = paired_delta({"a": 0.5, "b": 0.5}, {"b": 0.9, "c": 0.1})
    assert delta.n == 1
