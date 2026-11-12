"""Per-sample RAGAS scores, reference-only metrics applied only where a reference exists."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from callobuzz_rag.evaluation import scoring
from callobuzz_rag.evaluation.metrics import (
    ALL_METRICS,
    ANSWER_CORRECTNESS,
    CONTEXT_RECALL,
    CONTEXT_RELEVANCE,
    MetricThresholds,
)
from callobuzz_rag.evaluation.scoring import ScoringSample


def test_thresholds_take_overrides() -> None:
    t = MetricThresholds().with_overrides({"answer_correctness": 0.9, "unknown": 0.1})
    assert t.get(ANSWER_CORRECTNESS) == 0.9 and t.get(CONTEXT_RELEVANCE) == 0.7
    assert len(ALL_METRICS) == 6


def test_out_of_range_overrides_are_ignored() -> None:
    t = MetricThresholds().with_overrides({"faithfulness": 7.0})
    assert t.faithfulness == 0.9


def test_ragas_row_keys_cover_both_spellings() -> None:
    assert scoring.RAGAS_NAMES["factual_correctness"] == ANSWER_CORRECTNESS
    assert scoring.RAGAS_NAMES["factual_correctness(mode=f1)"] == ANSWER_CORRECTNESS
    assert scoring.RAGAS_NAMES["nv_context_relevance"] == CONTEXT_RELEVANCE


def _run(samples, row_for):
    calls: list[tuple[int, list[str]]] = []

    def fake_evaluate(dataset, metrics, **kwargs):
        names = [type(m).__name__ for m in metrics]
        calls.append((len(dataset.samples), names))
        return SimpleNamespace(scores=[row_for(names)] * len(dataset.samples))

    with (
        patch.object(scoring, "_evaluate", side_effect=fake_evaluate),
        patch.object(scoring, "RagasEmbeddings") as emb,
        patch.object(scoring, "build_ragas_llm", return_value=MagicMock()),
    ):
        emb.return_value.__enter__.return_value = SimpleNamespace(ragas=object())
        out = scoring.score_samples(samples, judge_config=MagicMock(), settings=MagicMock())
    return out, calls


def _row(names, key="factual_correctness"):
    row = {
        "faithfulness": 0.9,
        "answer_relevancy": 0.8,
        "llm_context_precision_without_reference": 0.7,
        "nv_context_relevance": 0.6,
    }
    if "FactualCorrectness" in names:
        row |= {key: 0.75, "context_recall": 0.5}
    return row


def test_reference_metrics_only_on_samples_with_a_reference() -> None:
    samples = [
        ScoringSample(
            "Fasting for lipid profile?",
            "10-12 hours [1]",
            ["Fast 10-12 hours"],
            "Fast 10-12 hours",
        ),
        ScoringSample("Hi", "Hello! How can I help?", ["..."], None),
    ]
    out, calls = _run(samples, _row)
    assert sorted(c[0] for c in calls) == [1, 1]
    assert out[0][ANSWER_CORRECTNESS] == 0.75 and out[0][CONTEXT_RECALL] == 0.5
    assert out[1][ANSWER_CORRECTNESS] is None and out[1][CONTEXT_RELEVANCE] == 0.6


def test_the_old_row_key_spelling_still_maps() -> None:
    samples = [ScoringSample("q", "a", ["c"], "ref")]
    out, _ = _run(samples, lambda n: _row(n, "factual_correctness(mode=f1)"))
    assert out[0][ANSWER_CORRECTNESS] == 0.75


def test_nan_is_not_a_score() -> None:
    samples = [ScoringSample("q", "a", ["c"], None)]
    out, _ = _run(samples, lambda n: {"faithfulness": float("nan"), "answer_relevancy": 0.5})
    assert out[0]["faithfulness"] is None and out[0]["answer_relevancy"] == 0.5
