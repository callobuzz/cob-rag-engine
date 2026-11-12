"""Metric definitions, thresholds, and result models for RAGAS evaluation.

Metric names match RAGAS library metric class names (lowercased).
Thresholds are defaults from the Phase 3 design doc — configurable per run.
"""

from dataclasses import dataclass, replace

# --- Metric name constants ---
FAITHFULNESS = "faithfulness"
ANSWER_RELEVANCY = "answer_relevancy"
CONTEXT_PRECISION = "context_precision"
CONTEXT_RECALL = "context_recall"
ANSWER_CORRECTNESS = "answer_correctness"
CONTEXT_RELEVANCE = "context_relevance"
# Owner-defined judge criteria are scored as "criterion:<name>" (conversation C7).
CRITERION_PREFIX = "criterion:"

ALL_METRICS: list[str] = [
    FAITHFULNESS,
    ANSWER_RELEVANCY,
    CONTEXT_PRECISION,
    CONTEXT_RECALL,
    ANSWER_CORRECTNESS,
    CONTEXT_RELEVANCE,
]
# Metrics that compare against a reference answer: scored only where one exists.
REFERENCE_METRICS = frozenset({CONTEXT_RECALL, ANSWER_CORRECTNESS})


@dataclass
class MetricThresholds:
    """Target thresholds for each metric. Scores at or above pass."""

    faithfulness: float = 0.9
    answer_relevancy: float = 0.85
    context_precision: float = 0.8
    context_recall: float = 0.75
    answer_correctness: float = 0.7
    context_relevance: float = 0.7

    def get(self, metric_name: str) -> float:
        """Get threshold for a metric name. Returns 0.0 for unknown metrics."""
        mapping = {
            FAITHFULNESS: self.faithfulness,
            ANSWER_RELEVANCY: self.answer_relevancy,
            CONTEXT_PRECISION: self.context_precision,
            CONTEXT_RECALL: self.context_recall,
            ANSWER_CORRECTNESS: self.answer_correctness,
            CONTEXT_RELEVANCE: self.context_relevance,
        }
        return mapping.get(metric_name, 0.0)

    def with_overrides(self, overrides: dict[str, float]) -> "MetricThresholds":
        """A copy with the known metrics' thresholds replaced (values outside 0..1 ignored)."""
        known = {
            k: float(v) for k, v in overrides.items() if k in ALL_METRICS and 0.0 <= float(v) <= 1.0
        }
        return replace(self, **known)


DEFAULT_THRESHOLDS = MetricThresholds()


@dataclass
class MetricResult:
    """Result of a single metric evaluation."""

    name: str
    score: float
    threshold: float

    @property
    def passed(self) -> bool:
        """True if score meets or exceeds threshold."""
        return self.score >= self.threshold

    def to_dict(self) -> dict[str, object]:
        """Serialize to dict for JSONB storage."""
        return {
            "name": self.name,
            "score": self.score,
            "threshold": self.threshold,
            "passed": self.passed,
        }
