"""Unit tests for evaluation metric definitions and thresholds."""

import pytest

from callobuzz_rag.core.exceptions import EvaluationError, RAGKernelError
from callobuzz_rag.evaluation.metrics import (
    ANSWER_RELEVANCY,
    CONTEXT_PRECISION,
    CONTEXT_RECALL,
    DEFAULT_THRESHOLDS,
    FAITHFULNESS,
    MetricResult,
    MetricThresholds,
)


class TestMetricConstants:
    def test_metric_names_are_strings(self) -> None:
        assert FAITHFULNESS == "faithfulness"
        assert ANSWER_RELEVANCY == "answer_relevancy"
        assert CONTEXT_PRECISION == "context_precision"
        assert CONTEXT_RECALL == "context_recall"

    def test_all_metrics_list(self) -> None:
        from callobuzz_rag.evaluation.metrics import ALL_METRICS

        assert len(ALL_METRICS) == 6
        assert FAITHFULNESS in ALL_METRICS


class TestMetricThresholds:
    def test_default_thresholds(self) -> None:
        t = DEFAULT_THRESHOLDS
        assert t.faithfulness == 0.9
        assert t.answer_relevancy == 0.85
        assert t.context_precision == 0.8
        assert t.context_recall == 0.75

    def test_custom_thresholds(self) -> None:
        t = MetricThresholds(faithfulness=0.95, answer_relevancy=0.9)
        assert t.faithfulness == 0.95
        assert t.answer_relevancy == 0.9
        # Defaults for unset
        assert t.context_precision == 0.8

    def test_get_threshold(self) -> None:
        t = DEFAULT_THRESHOLDS
        assert t.get(FAITHFULNESS) == 0.9
        assert t.get(CONTEXT_RECALL) == 0.75

    def test_get_unknown_metric_returns_zero(self) -> None:
        t = DEFAULT_THRESHOLDS
        assert t.get("unknown_metric") == 0.0


class TestMetricResult:
    def test_passed_above_threshold(self) -> None:
        r = MetricResult(name=FAITHFULNESS, score=0.95, threshold=0.9)
        assert r.passed is True

    def test_failed_below_threshold(self) -> None:
        r = MetricResult(name=FAITHFULNESS, score=0.85, threshold=0.9)
        assert r.passed is False

    def test_passed_at_exact_threshold(self) -> None:
        r = MetricResult(name=FAITHFULNESS, score=0.9, threshold=0.9)
        assert r.passed is True

    def test_to_dict(self) -> None:
        r = MetricResult(name=FAITHFULNESS, score=0.95, threshold=0.9)
        d = r.to_dict()
        assert d == {
            "name": "faithfulness",
            "score": 0.95,
            "threshold": 0.9,
            "passed": True,
        }


class TestEvaluationError:
    def test_inherits_from_base(self) -> None:
        assert issubclass(EvaluationError, RAGKernelError)

    def test_can_be_raised(self) -> None:
        with pytest.raises(EvaluationError, match="eval failed"):
            raise EvaluationError("eval failed")
