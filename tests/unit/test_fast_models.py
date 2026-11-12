"""benchmarks/results/fast-models.json: the one source of the fast-model table (engine API,
dashboard, docs, setup wizard). Its bar results, tags and recommendations are derived from the
measurements by `callobuzz_rag.core.benchmark_results.normalise`; the file must equal that."""

import json
from typing import Any

import pytest

from callobuzz_rag.core.benchmark_results import (
    FAST_MODELS_PATH,
    FastModelBenchmarks,
    load_fast_models,
    normalise,
)

BARS = {
    "overall": 0.90,
    "per_language": 0.85,
    "business_recall": 0.97,
    "reply_language": 0.95,
    "max_fallback_rate": 0.05,
}


def _row(model: str, p90: float, usd: float, overall: float = 0.95, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "model": model,
        "provider": "openrouter",
        "provider_order": [],
        "reasoning_effort": None,
        "thinking_control_verified": False,
        "date": "2026-10-03",
        "report": f"labels-{model}.md",
        "turns": 266,
        "model_turns": 162,
        "overall": overall,
        "per_language": {"en": 0.95, "hi": 0.95, "hi-Latn": 0.95},
        "business_recall": 0.99,
        "reply_language": 1.0,
        "fallback_rate": 0.0,
        "latency_s": {"p50": p90 - 0.2, "p90": p90, "p99": None},
        "usd_per_1000_labels": usd,
        "cached_prompt_share": None,
    }
    row.update(over)
    return row


def _doc(*rows: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "updated": "2026-10-03",
        "method": {"summary": "s", "link": "https://example.org/m"},
        "bars": BARS,
        "models": list(rows),
    }


def test_the_shipped_file_is_valid_and_normalised() -> None:
    raw = json.loads(FAST_MODELS_PATH.read_text("utf-8"))
    assert raw == normalise(raw), "run: make bench-tables"
    data = load_fast_models()
    assert data is not None and data.models
    keys = {m.key for m in data.models}
    for value in data.recommendations.model_dump().values():
        assert value is None or value in keys


def test_the_default_has_the_best_business_recall_then_accuracy_then_the_lowest_p90() -> None:
    out = normalise(
        _doc(
            _row("a/fast-but-wrong", 0.4, 0.05, overall=0.80),
            _row("b/quick", 0.9, 0.30, business_recall=0.98, overall=0.97),
            _row("c/quicker-pinned", 0.7, 0.40, provider_order=["groq"]),
            _row("d/slow-cheap", 2.5, 0.02, overall=0.97),
            _row("e/slow-best", 3.0, 0.50, business_recall=0.995, overall=0.93),
        )
    )
    rec = out["recommendations"]
    # e: highest business-question recall of the five, though it is the slowest and dearest.
    assert rec["default"] == "e/slow-best"
    assert rec["fastest_passing"] == "c/quicker-pinned@groq"
    assert rec["budget"] == rec["cheapest_passing"] == "d/slow-cheap"
    assert rec["most_accurate"] == "b/quick"
    wrong = next(m for m in out["models"] if m["model"] == "a/fast-but-wrong")
    assert wrong["passes"] is False and wrong["bars"]["overall"] is False
    assert wrong["tags"] == []
    assert next(m for m in out["models"] if m["key"] == "c/quicker-pinned@groq")["tags"] == [
        "fastest"
    ]
    assert next(m for m in out["models"] if m["key"] == "e/slow-best")["tags"] == ["recommended"]


def test_recall_ties_fall_to_overall_accuracy_then_to_latency() -> None:
    out = normalise(
        _doc(
            _row("a/x", 1.0, 0.3, overall=0.95),
            _row("b/y", 2.0, 0.3, overall=0.97),
            _row("c/z", 1.5, 0.3, overall=0.97),
        )
    )
    assert out["recommendations"]["default"] == "c/z"  # tied on recall and accuracy: faster


def test_the_budget_pick_is_the_cheapest_passing_model_and_none_without_a_price() -> None:
    out = normalise(
        _doc(
            _row("a/x", 1.7, 0.5),
            _row("b/y", 3.1, 0.1),
            _row("c/free-but-failing", 0.5, 0.0, overall=0.5),
        )
    )
    assert out["recommendations"]["budget"] == "b/y"
    unpriced = normalise(_doc(_row("a/x", 1.7, 0.5, usd_per_1000_labels=None)))
    assert unpriced["recommendations"]["budget"] is None
    assert unpriced["recommendations"]["default"] == "a/x"


@pytest.mark.parametrize(
    ("over", "bar"),
    [
        ({"fallback_rate": 0.06}, "fallbacks"),
        ({"business_recall": 0.96}, "business_recall"),
        ({"reply_language": 0.94}, "reply_language"),
        ({"per_language": {"en": 0.99, "hi": 0.84, "hi-Latn": 0.99}}, "per_language"),
    ],
)
def test_each_bar_can_fail_a_model(over: dict[str, Any], bar: str) -> None:
    out = normalise(_doc(_row("a/x", 0.5, 0.1, **over)))
    row = out["models"][0]
    assert row["passes"] is False and row["bars"][bar] is False
    assert out["recommendations"]["default"] is None


def test_unknown_fields_are_refused() -> None:
    doc = normalise(_doc(_row("a/x", 0.5, 0.1)))
    doc["models"][0]["surprise"] = 1
    with pytest.raises(ValueError):
        FastModelBenchmarks.model_validate(doc)


def test_the_shipped_summary_is_valid() -> None:
    from callobuzz_rag.core.benchmark_results import load_summary

    summary = load_summary()
    assert summary is not None
    assert {s.id for s in summary.sections} >= {"beir", "documents", "labels", "ttft"}
    for section in summary.sections:
        assert all(len(r.values) == len(section.columns) for r in section.rows), section.id
