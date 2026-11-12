"""scripts/setup.py on OpenRouter: the fast-model menu (recommended model and the budget option,
every number from benchmarks/results/fast-models.json) and the thinking level of the main model."""

import json
import re
from pathlib import Path

import pytest

from callobuzz_rag.core.config import Settings
from tests.unit.test_setup_wizard import TEMPLATE, feed, wizard

LUNA = "openai/gpt-6-luna"
GEMMA_SMALL = "google/gemma-4-26b-a4b-it"
OPENROUTER = wizard.LLM_PROVIDER_BY_ID["openrouter"]


def _run(
    target: Path, monkeypatch: pytest.MonkeyPatch, answers: list[str]
) -> tuple[list[str], list[str]]:
    asked = feed(monkeypatch, answers + [""] * 40)
    said: list[str] = []
    monkeypatch.setattr(wizard, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(wizard.getpass, "getpass", lambda prompt="": "sk-or-test")
    code = wizard.main(
        ["--quick", "--env-file", str(target), "--template", str(TEMPLATE)], out=said.append
    )
    assert code == 0
    return asked, said


def _settings(path: Path) -> Settings:
    return Settings(_env_file=path)  # type: ignore[call-arg]


def test_the_measured_picks_are_luna_and_the_budget_gemma() -> None:
    best = wizard.measured_fast_model()
    budget = wizard.measured_budget_model()
    assert best is not None and best.model == LUNA
    assert budget is not None and budget.model == GEMMA_SMALL


def test_the_menu_lines_carry_the_numbers_of_the_benchmark_file() -> None:
    best = wizard.measured_fast_model()
    budget = wizard.measured_budget_model()
    assert best is not None and budget is not None
    recommended, budget_line = wizard.fast_choice_labels(best, budget)
    assert budget_line is not None
    assert re.fullmatch(
        r"Recommended: openai/gpt-6-luna - most accurate \(98\.9%\), "
        r"(₹|Rs )13\.6 per 1,000 messages",
        recommended,
    )
    assert re.fullmatch(
        r"Budget: google/gemma-4-26b-a4b-it - 64% cheaper "
        r"\((₹|Rs )4\.9 per 1,000\), 97\.7% accurate",
        budget_line,
    )


def _write_file(path: Path, recommendations: dict[str, str], rows: list[dict]) -> None:
    for row in rows:
        row.update(provider_order=[], latency_s={"p90": 1.0})
    path.write_text(
        json.dumps({"recommendations": recommendations, "models": rows}), encoding="utf-8"
    )


def test_the_menu_reads_whatever_the_file_says(tmp_path: Path) -> None:
    path = tmp_path / "fast.json"
    _write_file(
        path,
        {"default": "a/best", "budget": "b/cheap", "most_accurate": "x"},
        [
            {"key": "a/best", "model": "a/best", "overall": 0.99, "usd_per_1000_labels": 0.2},
            {"key": "b/cheap", "model": "b/cheap", "overall": 0.95, "usd_per_1000_labels": 0.1},
        ],
    )
    best, budget = wizard.measured_fast_model(path), wizard.measured_budget_model(path)
    assert best is not None and budget is not None
    recommended, budget_line = wizard.fast_choice_labels(best, budget)
    assert "a/best - best on business questions (99.0%)" in recommended
    assert budget_line is not None
    assert "50% cheaper" in budget_line and "95.0% accurate" in budget_line


def test_no_budget_when_the_file_names_none_or_the_same_model(tmp_path: Path) -> None:
    path = tmp_path / "fast.json"
    row = {"key": "a/b", "model": "a/b", "overall": 0.9, "usd_per_1000_labels": 0.1}
    _write_file(path, {"default": "a/b"}, [dict(row)])
    assert wizard.measured_budget_model(path) is None
    _write_file(path, {"default": "a/b", "budget": "a/b"}, [dict(row)])
    assert wizard.measured_budget_model(path) is None


def test_the_menu_defaults_to_luna(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "envout"
    _, said = _run(
        target, monkeypatch, ["", "", "admin@yourcompany.in", "1", "", ""]
    )  # two menus, e-mail, openrouter, main model, fast menu
    text = "\n".join(said)
    assert "Which fast model?" in text
    assert "1) Recommended: openai/gpt-6-luna" in text and "(default)" in text
    assert "2) Budget: google/gemma-4-26b-a4b-it" in text
    settings = _settings(target)
    assert settings.llm_fast_model == LUNA
    assert settings.llm_model == "openai/gpt-oss-120b"
    assert settings.llm_thinking == "low"


def test_choosing_two_takes_the_budget_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "envout"
    _run(target, monkeypatch, ["", "", "admin@yourcompany.in", "1", "", "2"])
    settings = _settings(target)
    assert settings.llm_fast_model == GEMMA_SMALL
    assert settings.rerank_llm_model == GEMMA_SMALL


def test_choosing_another_model_asks_for_its_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "envout"
    _run(target, monkeypatch, ["", "", "admin@yourcompany.in", "1", "", "3", "openai/gpt-4.1-mini"])
    assert _settings(target).llm_fast_model == "openai/gpt-4.1-mini"


def test_yes_mode_writes_the_new_defaults(tmp_path: Path) -> None:
    target = tmp_path / "envout"
    code = wizard.main(
        ["--yes", "--env-file", str(target), "--template", str(TEMPLATE)], out=lambda _: None
    )
    assert code == 0
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["RAG_LLM_MODEL"] == "openai/gpt-oss-120b"
    assert values["RAG_LLM_THINKING"] == "low"
    assert values["RAG_LLM_FAST_MODEL"] == LUNA


def test_other_providers_keep_their_text_question(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "envout"
    asked, said = _run(
        target, monkeypatch, ["", "", "admin@yourcompany.in", "2", "", ""]
    )  # two menus, e-mail, openai, model, fast model
    assert "Which fast model?" not in "\n".join(said)
    assert any(a.startswith("Fast model") for a in asked)
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert values["RAG_LLM_FAST_MODEL"] == "gpt-4.1-mini"
    assert values["RAG_LLM_THINKING"] == ""  # the template's "low" is for gpt-oss
    assert values["RAG_LLM_LANGUAGE_MODELS"] == ""


def test_thinking_follows_the_model_family() -> None:
    values = {
        "RAG_LLM_PROVIDER": "openrouter",
        "RAG_LLM_MODEL": "openai/gpt-oss-120b",
        "RAG_LLM_THINKING": "low",
    }
    # The same model again: untouched. A model that needs no "low": the template's value goes.
    wizard.apply_llm(values, OPENROUTER, "openai/gpt-oss-120b", "sk")
    assert values["RAG_LLM_THINKING"] == "low"
    wizard.apply_llm(values, OPENROUTER, "anthropic/claude-sonnet-5-5", "sk")
    assert values["RAG_LLM_THINKING"] == ""
    # A gpt-oss model with nothing set gets "low"; a level you chose is kept.
    wizard.apply_llm(values, OPENROUTER, "openai/gpt-oss-20b", "sk")
    assert values["RAG_LLM_THINKING"] == "low"
    values["RAG_LLM_THINKING"] = "high"
    wizard.apply_llm(values, OPENROUTER, "openai/gpt-oss-120b", "sk")
    assert values["RAG_LLM_THINKING"] == "high"
    wizard.apply_llm(values, OPENROUTER, "anthropic/claude-sonnet-5-5", "sk")
    assert values["RAG_LLM_THINKING"] == "high"


def test_a_routing_map_on_another_provider_is_flagged() -> None:
    values = {
        "RAG_LLM_PROVIDER": "openai",
        "RAG_LLM_LANGUAGE_MODELS": '{"hi":"google/gemma-4-31b-it"}',
    }
    notes = wizard.consistency_warnings(values, super_admin_set=True)
    assert any("RAG_LLM_LANGUAGE_MODELS" in n for n in notes)
    values["RAG_LLM_PROVIDER"] = "openrouter"
    notes = wizard.consistency_warnings(values, super_admin_set=True)
    assert not any("RAG_LLM_LANGUAGE_MODELS" in n for n in notes)
