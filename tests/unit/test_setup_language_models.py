"""scripts/setup.py asks whether Hindi and Hinglish replies get their own model, on OpenRouter
only, and writes RAG_LLM_LANGUAGE_MODELS (llm/language_routing.py)."""

import json
from pathlib import Path

import pytest

from callobuzz_rag.core.config import Settings
from tests.unit.test_setup_wizard import TEMPLATE, feed, wizard

GEMMA = "google/gemma-4-31b-it"
ROUTES = {"hi": GEMMA, "hi-Latn": GEMMA}


def _settings(path: Path) -> Settings:
    return Settings(_env_file=path)  # type: ignore[call-arg]


def _run(target: Path, monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> list[str]:
    asked = feed(monkeypatch, answers)
    monkeypatch.setattr(wizard, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(wizard.getpass, "getpass", lambda prompt="": "sk-or-test")
    code = wizard.main(
        ["--quick", "--env-file", str(target), "--template", str(TEMPLATE)], out=lambda _: None
    )
    assert code == 0
    return asked


def test_apply_writes_the_benchmark_pick_for_openrouter() -> None:
    values: dict[str, str] = {}
    wizard.apply_language_models(values, wizard.LLM_PROVIDER_BY_ID["openrouter"], True)
    assert json.loads(values["RAG_LLM_LANGUAGE_MODELS"]) == ROUTES


def test_a_no_clears_it() -> None:
    values = {"RAG_LLM_LANGUAGE_MODELS": json.dumps(ROUTES)}
    wizard.apply_language_models(values, wizard.LLM_PROVIDER_BY_ID["openrouter"], False)
    assert values["RAG_LLM_LANGUAGE_MODELS"] == ""


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini", "ollama"])
def test_other_providers_never_get_an_openrouter_model_id(provider: str) -> None:
    values = {"RAG_LLM_LANGUAGE_MODELS": json.dumps(ROUTES)}
    wizard.apply_language_models(values, wizard.LLM_PROVIDER_BY_ID[provider], True)
    assert values["RAG_LLM_LANGUAGE_MODELS"] == ""


def test_a_map_already_in_the_file_is_kept() -> None:
    mine = json.dumps({"hi": "meta-llama/llama-4-scout"})
    values = {"RAG_LLM_LANGUAGE_MODELS": mine}
    wizard.apply_language_models(values, wizard.LLM_PROVIDER_BY_ID["openrouter"], True)
    assert values["RAG_LLM_LANGUAGE_MODELS"] == mine


def test_the_question_defaults_to_yes_and_the_engine_reads_the_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "envout"
    answers = ["1", "", "", "", "", ""]  # provider, model, fast model, then the rest: defaults
    asked = _run(target, monkeypatch, answers + [""] * 40)
    prompt = next(a for a in asked if "Hindi/Hinglish" in a)
    assert GEMMA in prompt and "[Y/n]" in prompt
    assert _settings(target).llm_language_models == ROUTES


def test_answering_no_leaves_routing_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "envout"
    queue = iter(["1", "", ""])  # openrouter, default model, default fast model
    asked = feed(monkeypatch, [])

    def answer(prompt: str = "") -> str:
        asked.append(prompt)
        if "Hindi/Hinglish" in prompt:
            return "n"
        return next(queue, "")

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(wizard, "_stdin_is_tty", lambda: True)
    monkeypatch.setattr(wizard.getpass, "getpass", lambda prompt="": "sk-or-test")
    code = wizard.main(
        ["--quick", "--env-file", str(target), "--template", str(TEMPLATE)], out=lambda _: None
    )
    assert code == 0
    assert _settings(target).llm_language_models == {}


def test_yes_mode_turns_it_on_for_openrouter(tmp_path: Path) -> None:
    target = tmp_path / "envout"
    code = wizard.main(
        ["--yes", "--env-file", str(target), "--template", str(TEMPLATE)], out=lambda _: None
    )
    assert code == 0
    values = wizard.parse_env_text(target.read_text(encoding="utf-8"))
    assert json.loads(values["RAG_LLM_LANGUAGE_MODELS"]) == ROUTES
    assert _settings(target).llm_language_models == ROUTES
