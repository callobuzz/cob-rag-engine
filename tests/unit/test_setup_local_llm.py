"""PIP2: the setup wizard's question about the free local model (no real env file)."""

import json
from pathlib import Path

import pytest

from tests.unit.test_setup_wizard import FULL_TAIL, interactive, run, wizard


@pytest.fixture(autouse=True)
def _linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wizard.platform, "system", lambda: "Linux")


@pytest.fixture
def target(tmp_path: Path) -> Path:
    return tmp_path / "envout"


# A fresh full run up to the optional services, then the same tail the other full runs use.
HEAD = ["1", "1", "", "6", "1", "1", "2", "1"]


def answers(local: str) -> list[str]:
    return [*HEAD, *FULL_TAIL[:-1], local]


def values_of(target: Path) -> dict[str, str]:
    return wizard.parse_env_text(target.read_text(encoding="utf-8"))


@pytest.fixture
def measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "local-llm.json"
    path.write_text(json.dumps({"default": "qwen3:4b", "best": "qwen3:4b"}), encoding="utf-8")
    monkeypatch.setattr(wizard, "LOCAL_LLM_PATH", path)
    return path


@pytest.fixture
def unmeasured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wizard, "LOCAL_LLM_PATH", tmp_path / "missing.json")


def test_yes_adds_the_profile_and_enables_it(
    monkeypatch: pytest.MonkeyPatch, target: Path, unmeasured: None
) -> None:
    code, output, _ = interactive(monkeypatch, target, ["--full"], answers("y"))
    assert code == 0
    values = values_of(target)
    assert values["RAG_LOCAL_LLM_ENABLED"] == "true"
    assert "local-llm" in values["COMPOSE_PROFILES"].split(",")
    assert values["RAG_LOCAL_LLM_MODEL"] == wizard.LOCAL_LLM_PLACEHOLDER
    assert "about 6 GB of free memory" in output and "10-40 seconds" in output


def test_the_measured_default_is_offered_when_a_benchmark_exists(
    monkeypatch: pytest.MonkeyPatch, target: Path, measured: Path
) -> None:
    code, _, _ = interactive(monkeypatch, target, ["--full"], answers("y"))
    assert code == 0
    assert values_of(target)["RAG_LOCAL_LLM_MODEL"] == "qwen3:4b"


def test_no_removes_the_profile(
    monkeypatch: pytest.MonkeyPatch, target: Path, unmeasured: None
) -> None:
    target.write_text(
        "COMPOSE_PROFILES=local-llm\nRAG_LOCAL_LLM_ENABLED=true\n", encoding="utf-8"
    )
    # An existing file also asks whether to back it up and replace it.
    code, _, _ = interactive(monkeypatch, target, ["--full"], [*answers("n"), "y"])
    assert code == 0
    values = values_of(target)
    assert values["RAG_LOCAL_LLM_ENABLED"] == "false"
    assert "local-llm" not in values["COMPOSE_PROFILES"].split(",")


def test_quick_does_not_ask_and_keeps_the_current_choice(target: Path, unmeasured: None) -> None:
    target.write_text(
        "COMPOSE_PROFILES=tei,local-llm\nRAG_LOCAL_LLM_ENABLED=true\n"
        "RAG_LOCAL_LLM_MODEL=llama3.2:3b\n",
        encoding="utf-8",
    )
    code, lines = run(["--quick", "--yes"], target)
    assert code == 0
    assert not any("free answers" in line.lower() for line in lines)
    values = values_of(target)
    assert values["RAG_LOCAL_LLM_ENABLED"] == "true"
    assert values["RAG_LOCAL_LLM_MODEL"] == "llama3.2:3b"
    assert "local-llm" in values["COMPOSE_PROFILES"].split(",")


def test_set_wins_over_the_default(target: Path, measured: Path) -> None:
    code, _ = run(
        [
            "--full",
            "--yes",
            "--set",
            "RAG_LOCAL_LLM_ENABLED=true",
            "--set",
            "RAG_LOCAL_LLM_MODEL=phi4-mini",
        ],
        target,
    )
    assert code == 0
    assert values_of(target)["RAG_LOCAL_LLM_MODEL"] == "phi4-mini"


def test_an_existing_file_keeps_its_choice_on_a_rerun(target: Path, measured: Path) -> None:
    target.write_text(
        "COMPOSE_PROFILES=tei,local-llm\nRAG_LOCAL_LLM_ENABLED=true\n"
        "RAG_LOCAL_LLM_MODEL=llama3.2:3b\n",
        encoding="utf-8",
    )
    # Every default: the yes/no question defaults to the file's own answer (on), the model is kept.
    code, _ = run(["--full", "--yes"], target)
    assert code == 0
    values = values_of(target)
    assert values["RAG_LOCAL_LLM_ENABLED"] == "true"
    assert values["RAG_LOCAL_LLM_MODEL"] == "llama3.2:3b"


def test_apply_local_llm_orders_profiles_and_leaves_others() -> None:
    values = {"COMPOSE_PROFILES": "legacy-dashboard,tei"}
    wizard.apply_local_llm(values, True, "gemma3:4b")
    assert values["COMPOSE_PROFILES"] == "tei,local-llm,legacy-dashboard"
    wizard.apply_local_llm(values, False)
    assert values["COMPOSE_PROFILES"] == "tei,legacy-dashboard"
    assert values["RAG_LOCAL_LLM_ENABLED"] == "false"


def test_apply_profiles_does_not_drop_the_local_profile() -> None:
    values = {"COMPOSE_PROFILES": "local-llm"}
    wizard.apply_profiles(values, observability=False, transcription=False)
    assert "local-llm" in values["COMPOSE_PROFILES"].split(",")


def test_an_unreadable_benchmark_file_means_no_measured_default(tmp_path: Path) -> None:
    bad = tmp_path / "x.json"
    bad.write_text("{nope", encoding="utf-8")
    assert wizard.measured_local_model(bad) is None
    assert wizard.measured_local_model(tmp_path / "gone.json") is None
