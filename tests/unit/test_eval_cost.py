"""Run cost estimates from token assumptions and the price table."""

from callobuzz_rag.analytics.cost_tracker import CostTracker
from callobuzz_rag.evaluation.cost import estimate_run_cost

PRICES = {
    "answer-model": {"prompt_per_1k": 0.001, "completion_per_1k": 0.002},
    "judge-model": {"prompt_per_1k": 0.0005, "completion_per_1k": 0.001},
}


def test_full_run_estimate() -> None:
    est = estimate_run_cost(
        10,
        "full",
        "answer-model",
        "judge-model",
        metric_count=6,
        criteria_count=1,
        tracker=CostTracker(pricing_overrides=PRICES),
    )
    # answers: 10 x (2.5k x 0.001 + 0.3k x 0.002) = 0.031
    assert round(est.answer_usd, 4) == 0.031
    # judge: 10 x 7 calls x (1.8k x 0.0005 + 0.25k x 0.001) = 0.0805
    assert round(est.judge_usd, 4) == 0.0805
    assert round(est.total_usd, 4) == 0.1115 and est.unpriced_models == []
    assert est.assumptions["judge_calls_per_question"] == 7


def test_fast_run_is_free() -> None:
    est = estimate_run_cost(
        50,
        "fast",
        "answer-model",
        "judge-model",
        6,
        2,
        tracker=CostTracker(pricing_overrides=PRICES),
    )
    assert est.total_usd == 0.0 and est.unpriced_models == []


def test_unknown_prices_are_listed_not_priced_as_free() -> None:
    est = estimate_run_cost(
        5, "full", "mystery-a", "mystery-b", 4, 0, tracker=CostTracker(pricing_overrides={})
    )
    assert est.total_usd == 0.0 and est.unpriced_models == ["mystery-a", "mystery-b"]
