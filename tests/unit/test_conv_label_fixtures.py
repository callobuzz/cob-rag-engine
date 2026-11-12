# tests/unit/test_conv_label_fixtures.py
"""C1 label benchmark, LLM-free part: the fixture set is balanced, the rules are never
wrong, and language detection clears its bar. The model part is benchmarks/labels.py."""

import json
from collections import Counter
from pathlib import Path

import pytest

from callobuzz_rag.conversation.language import detect_language
from callobuzz_rag.conversation.rules import rule_labeler

FIXTURES = Path(__file__).parents[1] / "fixtures" / "conversation" / "labels.jsonl"
ROWS = [json.loads(line) for line in FIXTURES.read_text("utf-8").splitlines() if line.strip()]
MINIMUM = {
    "smalltalk": 14,
    "business_question": 22,
    "domain_explainer": 8,
    "off_topic": 10,
    "buying_intent": 10,
    "human_request": 8,
    "abuse": 4,
    "other": 4,
}
RULE_MINIMUM = {"smalltalk": 10, "buying_intent": 4, "human_request": 6, "abuse": 4}
LANGS = ("en", "hi", "hi-Latn")


def test_size_balance_and_unique_texts() -> None:
    assert len(ROWS) >= 240
    assert len({r["text"] for r in ROWS}) == len(ROWS)
    assert len({r["id"] for r in ROWS}) == len(ROWS)
    per = Counter((r["label"], r["language"]) for r in ROWS)
    for lang in LANGS:
        for label, n in MINIMUM.items():
            assert per[(label, lang)] >= n, (label, lang, per[(label, lang)])
        rules = Counter(r["label"] for r in ROWS if r["language"] == lang and r["rule_expected"])
        for label, n in RULE_MINIMUM.items():
            assert rules[label] >= n, (label, lang)
        follow_ups = [r for r in ROWS if r["language"] == lang and r["history"]]
        assert len(follow_ups) >= 10, lang


@pytest.mark.parametrize("row", [r for r in ROWS if r["rule_expected"]], ids=lambda r: r["id"])
def test_rules_decide_their_rows(row: dict) -> None:
    assert rule_labeler().classify(row["text"]).label == row["label"]


def test_rules_never_produce_a_wrong_label() -> None:
    wrong = [
        (r["id"], r["label"], str(out.label))
        for r in ROWS
        if (out := rule_labeler().classify(r["text"])).label is not None and out.label != r["label"]
    ]
    assert wrong == []


def test_language_detection_bar() -> None:
    hits = sum(1 for r in ROWS if detect_language(r["text"]).code == r["language"])
    assert hits / len(ROWS) >= 0.95, hits / len(ROWS)
    for lang in LANGS:
        rows = [r for r in ROWS if r["language"] == lang]
        lang_hits = sum(1 for r in rows if detect_language(r["text"]).code == lang)
        assert lang_hits / len(rows) >= 0.90, (lang, lang_hits / len(rows))


def _bench() -> object:
    import importlib
    import sys

    root = Path(__file__).parents[2]
    for extra in (root / "benchmarks", root / "clients" / "python"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    return importlib.import_module("labels")


def _result(expected: str, got: str, source: str, lang: str = "en") -> dict:
    return {
        "id": "x",
        "expected": expected,
        "got": got,
        "source": source,
        "language": lang,
        "got_language": lang,
    }


def test_benchmark_reports_the_fallback_rate_and_fails_a_run_that_mostly_fell_back() -> None:
    bench = _bench()
    # Every model-decided turn timed out: business_question recall looks perfect, the run is not.
    results = [_result("business_question", "business_question", "fallback") for _ in range(20)]
    results += [_result("smalltalk", "smalltalk", "rules") for _ in range(20)]
    summary = bench.summarise(results)  # type: ignore[attr-defined]
    assert summary["fallback_rate"] == 1.0 and summary["model_rows"] == 20
    assert summary["business_recall"] == 1.0
    assert not bench.passed(summary)  # type: ignore[attr-defined]


def test_benchmark_passes_a_clean_run() -> None:
    bench = _bench()
    results = []
    for lang in LANGS:
        results += [_result("business_question", "business_question", "model", lang)] * 30
        results += [_result("smalltalk", "smalltalk", "rules", lang)] * 10
    summary = bench.summarise(results)  # type: ignore[attr-defined]
    assert bench.passed(summary)  # type: ignore[attr-defined]


DEV = [
    json.loads(line)
    for line in (FIXTURES.parent / "labels_dev.jsonl").read_text("utf-8").splitlines()
    if line.strip()
]
BUSINESSES = json.loads((FIXTURES.parent / "label_businesses.json").read_text("utf-8"))


def test_dev_set_is_separate_from_the_held_out_benchmark() -> None:
    """Prompt and rules are tuned on labels_dev.jsonl; labels.jsonl stays held out."""
    assert len(DEV) >= 150
    assert len({r["id"] for r in DEV}) == len(DEV)
    assert not {r["text"].lower() for r in DEV} & {r["text"].lower() for r in ROWS}
    for lang in LANGS:
        assert len({r["label"] for r in DEV if r["language"] == lang}) == len(MINIMUM), lang


def test_every_row_names_a_known_business() -> None:
    from callobuzz_rag.conversation.templates import TEMPLATE_KEYS

    assert {r["business"] for r in ROWS + DEV} <= set(BUSINESSES)
    assert all(b["template"] in TEMPLATE_KEYS for b in BUSINESSES.values())


def test_rules_never_produce_a_wrong_label_on_the_dev_set() -> None:
    wrong = [
        (r["id"], r["label"], str(out.label))
        for r in DEV
        if (out := rule_labeler().classify(r["text"])).label is not None and out.label != r["label"]
    ]
    assert wrong == []


def test_the_label_prompt_quotes_no_held_out_benchmark_turn() -> None:
    """Few-shot examples must come from elsewhere, or the benchmark measures recall of the
    prompt. One- and two-word acknowledgements ("ok", "hmm") name the label's own definition
    and are allowed."""
    from callobuzz_rag.conversation.labeler import LABEL_PROMPT

    prompt = LABEL_PROMPT.lower()
    quoted = [r["id"] for r in ROWS if len(r["text"].split()) > 2 and r["text"].lower() in prompt]
    assert quoted == []
