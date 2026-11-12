"""PIP2: the deployer's local-model routes, /info and the benchmark rows."""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from callobuzz_rag.core import local_benchmarks
from callobuzz_rag.core.config import Settings
from callobuzz_rag.llm import local as local_mod
from callobuzz_rag.llm.local import local_status, reset_cache
from tests.unit.scope_harness import ADMIN_KEY, call, scoped_app


def local_settings(**kw: Any) -> Settings:
    return Settings(
        _env_file=None,
        admin_api_key=ADMIN_KEY,
        llm_api_key="k",
        local_llm_enabled=True,
        local_llm_url="http://user:secret@local-llm:11434",
        local_llm_model="gemma3:4b",
        **kw,
    )


def transport(*models: str) -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda r: httpx.Response(200, json={"models": [{"name": m} for m in models]})
    )


@pytest.fixture(autouse=True)
def _clean() -> None:
    reset_cache()


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Make the route's live check use a mock server."""
    holder: dict[str, httpx.MockTransport] = {"t": transport("gemma3:4b")}
    real = local_mod.local_status

    async def patched(settings: Any, **kw: Any) -> Any:
        return await real(settings, transport=holder["t"], warm=False)

    monkeypatch.setattr("callobuzz_rag.api.routes.local_llm.local_status", patched)
    return holder


async def test_status_per_state(probe: Any) -> None:
    with scoped_app() as (app, _):
        app.state.settings = local_settings()
        ready = await call(app, "admin", "GET", "/api/v1/admin/local-llm")
        probe["t"] = transport("other:1b")
        pulling = await call(app, "admin", "GET", "/api/v1/admin/local-llm")
        probe["t"] = httpx.MockTransport(
            lambda r: (_ for _ in ()).throw(httpx.ConnectError("x", request=r))
        )
        down = await call(app, "admin", "GET", "/api/v1/admin/local-llm")
    assert ready.json()["state"] == "ready" and ready.json()["models"] == ["gemma3:4b"]
    assert pulling.json()["state"] == "pulling" and down.json()["state"] == "down"
    assert "secret" not in ready.text and "local-llm" not in ready.text


async def test_status_when_not_set_up() -> None:
    with scoped_app() as (app, _):
        res = await call(app, "admin", "GET", "/api/v1/admin/local-llm")
    assert res.status_code == 200
    assert res.json()["state"] == "not_configured" and res.json()["enabled"] is False


class _Chunks:
    def __init__(self, parts: list[str]) -> None:
        self._parts = parts

    def __aiter__(self) -> AsyncIterator[Any]:
        async def gen() -> AsyncIterator[Any]:
            for p in self._parts:
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=p))])

        return gen()


@pytest.fixture
def fake_litellm(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import litellm

    seen: dict[str, Any] = {}

    async def acompletion(**kwargs: Any) -> _Chunks:
        seen.update(kwargs)
        return _Chunks(["नमस्ते", "! ", "OK"])

    monkeypatch.setattr(litellm, "acompletion", acompletion)
    return seen


async def test_the_test_route_reports_speed(fake_litellm: dict[str, Any]) -> None:
    with scoped_app() as (app, _):
        app.state.settings = local_settings()
        await local_status(app.state.settings, transport=transport("gemma3:4b"), warm=False)
        res = await call(app, "admin", "POST", "/api/v1/admin/local-llm/test")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["answer"] == "नमस्ते! OK"
    assert body["first_token_ms"] is not None and body["total_ms"] >= body["first_token_ms"]
    assert fake_litellm["custom_llm_provider"] == "ollama_chat"
    assert fake_litellm["max_tokens"] == 32 and fake_litellm["api_key"] == "local"


async def test_the_test_route_needs_a_ready_model() -> None:
    with scoped_app() as (app, _):
        app.state.settings = local_settings()
        res = await call(app, "admin", "POST", "/api/v1/admin/local-llm/test")
    assert res.status_code == 503 and res.json()["code"] == "local_llm_unavailable"


async def test_the_test_route_waits_ten_seconds(fake_litellm: dict[str, Any]) -> None:
    with scoped_app() as (app, _):
        app.state.settings = local_settings()
        await local_status(app.state.settings, transport=transport("gemma3:4b"), warm=False)
        first = await call(app, "admin", "POST", "/api/v1/admin/local-llm/test")
        second = await call(app, "admin", "POST", "/api/v1/admin/local-llm/test")
    assert first.status_code == 200
    assert second.status_code == 429 and second.json()["code"] == "local_llm_test_too_soon"
    assert int(second.headers["Retry-After"]) >= 1


@pytest.mark.parametrize("key", ["tk_a", "ck_a", "owner_a", "member_a"])
async def test_workspaces_are_refused(key: str) -> None:
    with scoped_app() as (app, _):
        got = await call(app, key, "GET", "/api/v1/admin/local-llm")
        posted = await call(app, key, "POST", "/api/v1/admin/local-llm/test")
    assert got.status_code == 403 and posted.status_code == 403


async def test_info_reports_the_local_model() -> None:
    with scoped_app() as (app, _):
        app.state.settings = local_settings()
        off = await call(app, "admin", "GET", "/api/v1/info")
        await local_status(app.state.settings, transport=transport("gemma3:4b"), warm=False)
        on = await call(app, "admin", "GET", "/api/v1/info")
    assert off.json()["features"]["local_llm"] == {"enabled": True, "ready": False}
    assert on.json()["features"]["local_llm"] == {"enabled": True, "ready": True}


ROW: dict[str, Any] = {
    "items": 10,
    "answered": 10,
    "errors": 0,
    "size_mb": 3300,
    "parameter_size": "4.3B",
    "quantization": "Q4_K_M",
    "refusal_on_answerable": 0.0,
    "ttft_p50_ms": 4000,
    "ttft_p90_ms": 6000,
    "total_p50_ms": 30000,
    "total_p90_ms": 42000,
    "tokens_per_s": 6.5,
}


def raw(*rows: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "updated": "2026-10-05",
        "run": "cpu-test",
        "method": "test",
        "hardware": {"cpu": "x", "cores": 8, "ram_gb": 16.0},
        "models": [{"model": m, **ROW, **over} for m, over in rows],
    }


def fact(overall: float, en: float, hi: float, hl: float) -> dict[str, Any]:
    return {"fact": {"overall": overall, "en": en, "hi": hi, "hi-Latn": hl}}


def test_the_passing_rule_and_best() -> None:
    doc = local_benchmarks.normalise(
        raw(
            ("a", fact(0.90, 0.95, 0.85, 0.80)),
            ("b", fact(0.92, 0.95, 0.60, 0.80)),  # a weak Hindi
            ("c", {**fact(0.90, 0.95, 0.85, 0.80), "total_p50_ms": 60000}),  # too slow
            ("d", {**fact(0.88, 0.9, 0.8, 0.8), "refusal_on_answerable": 0.2}),
        )
    )
    by = {r["model"]: r for r in doc["models"]}
    assert by["a"]["passed"] and by["a"]["fails"] == []
    assert by["b"]["fails"] == ["fact_language"]
    assert by["c"]["fails"] == ["too_slow"]
    assert by["d"]["fails"] == ["refusals"]
    assert doc["best"] == "a" and doc["default"] == "a" and doc["any_passed"] is True


def test_ties_go_to_the_faster_model() -> None:
    doc = local_benchmarks.normalise(
        raw(
            ("slow", {**fact(0.9, 0.9, 0.9, 0.9), "total_p50_ms": 40000}),
            ("quick", {**fact(0.9, 0.9, 0.9, 0.9), "total_p50_ms": 20000}),
        )
    )
    assert doc["best"] == "quick"


def test_when_none_passes_the_default_is_still_named() -> None:
    doc = local_benchmarks.normalise(
        raw(("x", fact(0.5, 0.6, 0.4, 0.5)), ("y", {**fact(0.6, 0.7, 0.5, 0.6), "total_p50_ms": 9}))
    )
    assert doc["best"] is None and doc["any_passed"] is False and doc["default"] == "y"


async def test_answer_benchmarks_carry_local_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "local-llm.json"
    path.write_text(json.dumps(raw(("gemma3:4b", fact(0.9, 0.9, 0.9, 0.9)))), encoding="utf-8")
    local_benchmarks.load_local_benchmarks.cache_clear()
    monkeypatch.setattr(
        "callobuzz_rag.api.routes.models.load_local_benchmarks",
        lambda: local_benchmarks.load_local_benchmarks(path),
    )
    with scoped_app() as (app, _):
        res = await call(app, "admin", "GET", "/api/v1/models/answer-benchmarks")
    local_benchmarks.load_local_benchmarks.cache_clear()
    assert res.status_code == 200, res.text
    rows = res.json()["local"]["models"]
    assert rows[0]["local"] is True and rows[0]["model"] == "gemma3:4b"
    assert rows[0]["fact"]["hi-Latn"] == 0.9


def test_a_missing_file_means_no_rows_never_an_error(tmp_path: Path) -> None:
    local_benchmarks.load_local_benchmarks.cache_clear()
    assert local_benchmarks.load_local_benchmarks(tmp_path / "nope.json") is None
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert local_benchmarks.load_local_benchmarks(tmp_path / "bad.json") is None
    local_benchmarks.load_local_benchmarks.cache_clear()
