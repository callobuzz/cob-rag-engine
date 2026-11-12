"""PIP2: the optional local-llm compose service (parsed as YAML, no Docker needed)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
SCRIPT = (ROOT / "scripts" / "entrypoint-local-llm.sh").read_bytes().decode("utf-8")
SERVICE = COMPOSE["services"]["local-llm"]


def test_it_is_an_optional_profile() -> None:
    assert SERVICE["profiles"] == ["local-llm"]


def test_the_image_is_pinned() -> None:
    image = SERVICE["image"]
    assert "ollama/ollama:" in image and "latest" not in image


def test_the_models_live_in_a_volume() -> None:
    assert "local_llm_models" in COMPOSE["volumes"]
    assert any(v.startswith("local_llm_models:") for v in SERVICE["volumes"])


def test_the_port_is_bound_to_the_configured_address() -> None:
    (port,) = SERVICE["ports"]
    assert port.startswith("${RAG_BIND_ADDR:-127.0.0.1}:") and port.endswith(":11434")


def test_health_means_the_model_is_there() -> None:
    health = SERVICE["healthcheck"]
    assert "ollama show" in " ".join(health["test"])
    assert health["start_period"] == "900s"


def test_the_entrypoint_is_mounted_read_only() -> None:
    assert any(
        v.endswith("entrypoint-local-llm.sh:/entrypoint-local-llm.sh:ro")
        for v in SERVICE["volumes"]
    )
    assert SERVICE["entrypoint"] == ["/bin/sh", "/entrypoint-local-llm.sh"]


def test_slots_context_and_keep_alive_follow_the_settings() -> None:
    env = SERVICE["environment"]
    assert "RAG_LOCAL_LLM_SLOTS" in env["OLLAMA_NUM_PARALLEL"]
    assert "RAG_LOCAL_LLM_KEEP_ALIVE" in env["OLLAMA_KEEP_ALIVE"]
    assert "RAG_LOCAL_LLM_NUM_CTX" in env["OLLAMA_CONTEXT_LENGTH"]


def test_the_engine_reaches_it_by_service_name() -> None:
    for name in ("app", "worker", "celery-beat", "mailer"):
        env = COMPOSE["services"][name].get("environment") or {}
        # The services share the x-rag-app anchor, which carries the variable.
        assert "RAG_LOCAL_LLM_URL" in env, name
        assert "RAG_LOCAL_LLM_URL_CONTAINER" in env["RAG_LOCAL_LLM_URL"]
        assert "http://local-llm:11434" in env["RAG_LOCAL_LLM_URL"]


def test_nothing_waits_for_it() -> None:
    for name, service in COMPOSE["services"].items():
        assert "local-llm" not in (service.get("depends_on") or {}), name


def test_the_gpu_overlay_repeats_the_profile() -> None:
    overlay = yaml.safe_load((ROOT / "docker-compose.gpu.yml").read_text(encoding="utf-8"))
    assert overlay["services"]["local-llm"]["profiles"] == ["local-llm"]
    assert "deploy" in overlay["services"]["local-llm"]


def test_the_script_is_posix_sh_and_unix_line_endings() -> None:
    assert SCRIPT.startswith("#!/bin/sh\n")
    assert "\r" not in SCRIPT
    assert "bash" not in SCRIPT and "[[" not in SCRIPT


def test_the_script_retries_the_pull_and_never_prints_the_environment() -> None:
    assert "ollama pull" in SCRIPT and "3" in SCRIPT
    for leaky in ("printenv", "env\n", "set -x", "$(env", "export -p"):
        assert leaky not in SCRIPT
    assert 'echo "$RAG' not in SCRIPT.replace("$MODEL", "")


def test_a_failed_download_stops_the_container_so_it_restarts() -> None:
    assert "break" not in SCRIPT
    assert "exit 1" in SCRIPT.split("Could not download", 1)[1]
    assert SERVICE["restart"] == "unless-stopped"
