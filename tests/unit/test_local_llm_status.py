"""PIP2: the local model's status, warm-up and the cache the answer path reads."""

import json

import httpx
import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.llm import local as local_mod
from callobuzz_rag.llm.local import (
    local_models,
    local_ready,
    local_status,
    reset_cache,
    safe_address,
    warm_up,
)


def settings(**kw: object) -> Settings:
    base: dict[str, object] = {"local_llm_enabled": True, "local_llm_model": "gemma3:4b"}
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def tags(*models: str, seen: list[httpx.Request] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in models]})
        return httpx.Response(200, json={"done": True})

    return httpx.MockTransport(handler)


def refuses(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("no server", request=request)


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    reset_cache()


@pytest.mark.parametrize(
    ("transport", "state"),
    [
        (tags("gemma3:4b", "qwen3:4b"), "ready"),
        (tags("qwen3:4b"), "pulling"),
        (httpx.MockTransport(refuses), "starting"),
    ],
)
async def test_states(transport: httpx.MockTransport, state: str) -> None:
    status = await local_status(settings(), transport=transport, warm=False)
    assert status.state == state
    assert local_ready(settings()) is (state == "ready")


async def test_a_bare_model_name_matches_its_latest_tag() -> None:
    s = settings(local_llm_model="llama3.2")
    status = await local_status(s, transport=tags("llama3.2:latest"), warm=False)
    assert status.state == "ready"


async def test_disabled_is_not_configured_without_any_request() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    status = await local_status(
        settings(local_llm_enabled=False), transport=httpx.MockTransport(fail)
    )
    assert status.state == "not_configured" and status.enabled is False


def test_local_ready_reads_the_cache_only(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*a: object, **k: object) -> None:
        raise AssertionError("local_ready must not do I/O")

    monkeypatch.setattr(httpx, "AsyncClient", explode)
    assert local_ready(settings()) is False  # a cold cache is not ready
    assert local_models(settings()) == ()


async def test_a_stale_cache_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    await local_status(settings(), transport=tags("gemma3:4b"), warm=False)
    assert local_ready(settings())
    later = local_mod.time.monotonic() + local_mod.STALE_AFTER_S + 1
    monkeypatch.setattr(local_mod.time, "monotonic", lambda: later)
    assert local_ready(settings()) is False  # the refresher died: do not trust the cache


async def test_a_changed_model_invalidates_the_cache() -> None:
    await local_status(settings(), transport=tags("gemma3:4b"), warm=False)
    assert local_ready(settings(local_llm_model="qwen3:4b")) is False


async def test_the_model_list_is_cached_for_the_language_map() -> None:
    await local_status(settings(), transport=tags("gemma3:4b", "qwen3:4b"), warm=False)
    assert local_models(settings()) == ("gemma3:4b", "qwen3:4b")


async def test_warm_up_runs_once_on_the_transition_to_ready() -> None:
    seen: list[httpx.Request] = []
    transport = tags("gemma3:4b", seen=seen)
    s = settings(local_llm_keep_alive="12h")
    await local_status(s, transport=transport)
    await local_status(s, transport=transport)
    generates = [r for r in seen if r.url.path == "/api/generate"]
    assert len(generates) == 1
    assert json.loads(generates[0].content) == {
        "model": "gemma3:4b",
        "prompt": "",
        "keep_alive": "12h",
    }


async def test_a_failed_warm_up_is_not_fatal() -> None:
    assert await warm_up(settings(), transport=httpx.MockTransport(refuses)) is False


def test_the_address_never_carries_credentials_or_paths() -> None:
    assert safe_address("http://user:secret@local-llm:11434/api/x?k=1") == "local-llm:11434"


async def test_status_never_logs_the_url_with_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    s = settings(local_llm_url="http://user:hunter2@127.0.0.1:11434")
    with caplog.at_level("DEBUG"):
        await local_status(s, transport=httpx.MockTransport(refuses), warm=False)
        await warm_up(s, transport=httpx.MockTransport(refuses))
    assert "hunter2" not in caplog.text


async def test_a_young_process_with_a_server_that_never_answered_is_starting() -> None:
    status = await local_status(settings(), transport=httpx.MockTransport(refuses), warm=False)
    assert status.state == "starting"
    assert local_ready(settings()) is False


async def test_a_server_that_answered_before_and_now_refuses_is_down() -> None:
    await local_status(settings(), transport=tags("gemma3:4b"), warm=False)
    status = await local_status(settings(), transport=httpx.MockTransport(refuses), warm=False)
    assert status.state == "down"


async def test_an_old_process_whose_server_never_answered_is_down(monkeypatch) -> None:
    monkeypatch.setattr(local_mod, "_STARTED", local_mod.time.monotonic() - 16 * 60)
    status = await local_status(settings(), transport=httpx.MockTransport(refuses), warm=False)
    assert status.state == "down"


async def test_ensure_status_looks_once_when_the_cache_is_empty(monkeypatch) -> None:
    calls: list[bool] = []

    async def fake(s: object, **kw: object) -> None:
        calls.append(bool(kw.get("warm", True)))

    monkeypatch.setattr(local_mod, "local_status", fake)
    await local_mod.ensure_status(settings())
    assert calls == [False]  # never warms the model
    calls.clear()
    await local_mod.ensure_status(settings(local_llm_enabled=False))
    assert calls == []


async def test_a_second_warm_up_while_one_runs_returns_at_once() -> None:
    async with local_mod._WARM_LOCK:
        assert await warm_up(settings(), transport=tags("gemma3:4b")) is False


async def test_a_worker_summary_looks_at_the_local_model_before_choosing_a_route(
    monkeypatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from callobuzz_rag.tiers import profiles as tier_profiles
    from callobuzz_rag.worker import conversation_tasks as tasks

    order: list[str] = []

    async def seen(s: object) -> None:
        order.append("status")

    async def planned(*a: object, **k: object) -> str:
        order.append("profile")
        return "profile"

    monkeypatch.setattr(local_mod, "ensure_status", seen)
    monkeypatch.setattr(tier_profiles, "plan_profile", planned)
    got = await tasks._profile_for(AsyncMock(), SimpleNamespace(tenant_id=1), settings())
    assert got == "profile" and order == ["status", "profile"]
