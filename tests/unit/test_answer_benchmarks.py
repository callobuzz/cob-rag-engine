"""The answer-model benchmark file, its derived rules and the API that serves it."""

import copy
import json

from callobuzz_rag.core.answer_benchmarks import (
    ANSWER_MODELS_PATH,
    BARS,
    HINDI_ALL,
    AnswerBenchmarks,
    is_open_weights,
    load_answer_models,
    normalise,
)

RAW = json.loads(ANSWER_MODELS_PATH.read_text("utf-8"))


def _doc() -> dict:
    return normalise(copy.deepcopy(RAW))


def _by_model(doc: dict, model: str) -> dict:
    return next(m for m in doc["models"] if m["model"] == model)


def test_the_published_file_is_one_item_set_and_valid() -> None:
    doc = _doc()
    doc["self_hostable"] = []
    parsed = AnswerBenchmarks.model_validate(doc)
    assert parsed.items.total == 94
    assert parsed.items.by_language == {"en": 54, "hi": 20, "hi-Latn": 20}
    assert len({m.key for m in parsed.models}) == len(parsed.models) == 19
    assert all(m.answered == m.items for m in parsed.models)
    assert 0.8 < parsed.spend.total_usd < 1.0


def test_the_recommendations_follow_the_rules() -> None:
    rec = _doc()["recommendations"]
    assert rec["default"] == "openai/gpt-oss-120b|low"
    assert rec["hindi_default"] == "google/gemma-4-31b-it|off"
    assert rec["hindi_budget"] == "google/gemma-4-26b-a4b-it|off"
    assert rec["best_hindi_tone"] == "anthropic/claude-haiku-4.5|off"
    assert rec["cheapest_passing"] == rec["most_accurate"] == rec["default"]


def test_a_model_that_invents_answers_never_meets_the_bar() -> None:
    doc = _doc()
    haiku = _by_model(doc, "anthropic/claude-haiku-4.5")
    assert not haiku["passes"] and "no_answer" in haiku["fails"]
    assert "best_hindi_tone" in haiku["tags"] and "recommended" not in haiku["tags"]
    for m in doc["models"]:
        if m["passes"]:
            assert m["refusal_correct_unanswerable"] >= BARS["no_answer_min"]
            assert m["passed"] >= BARS["pass_min"]
        if m["hindi_passes"]:
            assert m["passes"]
            assert m["by_language"][HINDI_ALL]["register_ok"] >= BARS["hindi_register_min"]


def test_every_pick_is_a_passing_model_except_the_tone() -> None:
    doc = _doc()
    rec = doc["recommendations"]
    rows = {m["key"]: m for m in doc["models"]}
    for name in ("default", "hindi_default", "hindi_budget", "fastest_passing", "cheapest_passing"):
        assert rows[rec[name]]["passes"], name
    assert [m["passes"] for m in doc["models"]] == sorted(
        (m["passes"] for m in doc["models"]), reverse=True
    )


def test_a_better_model_moves_the_default_without_touching_the_file() -> None:
    raw = copy.deepcopy(RAW)
    row = next(r for r in raw["rows"] if r["model"] == "google/gemma-4-26b-a4b-it")
    row["scores"]["passed"] = 0.99
    assert normalise(raw)["recommendations"]["default"] == "google/gemma-4-26b-a4b-it|off"
    row["scores"]["refusal_correct_unanswerable"] = 0.5  # now it invents answers
    assert normalise(raw)["recommendations"]["default"] == "openai/gpt-oss-120b|low"


def test_no_rows_means_no_recommendations() -> None:
    raw = copy.deepcopy(RAW)
    raw["rows"] = []
    rec = normalise(raw)["recommendations"]
    assert set(rec.values()) == {None}


def test_the_input_is_not_changed() -> None:
    raw = copy.deepcopy(RAW)
    normalise(raw)
    assert raw == RAW


def test_open_weights_by_family() -> None:
    assert is_open_weights("google/gemma-4-31b-it")
    assert is_open_weights("openai/gpt-oss-120b")
    assert not is_open_weights("openai/gpt-6-luna")
    assert not is_open_weights("anthropic/claude-haiku-4.5")
    assert not is_open_weights("google/gemini-2.5-flash")


def test_the_loader_serves_the_file_and_tolerates_its_absence(tmp_path) -> None:
    load_answer_models.cache_clear()
    assert load_answer_models(tmp_path / "missing.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", "utf-8")
    assert load_answer_models(bad) is None
    load_answer_models.cache_clear()
    served = load_answer_models()
    assert served is not None and "openai/gpt-oss-120b" in served.self_hostable
    load_answer_models.cache_clear()
