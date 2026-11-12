"""PIP review fixes: a model that cannot load is not ready; a status poll never skips the warm-up."""

import httpx
import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.llm import local as local_mod
from callobuzz_rag.llm.local import local_ready, local_status, reset_cache


def settings(**kw: object) -> Settings:
    base: dict[str, object] = {"local_llm_enabled": True, "local_llm_model": "gemma3:4b"}
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


def server(*, loads: list[bool], seen: list[httpx.Request] | None = None) -> httpx.MockTransport:
    """Lists the model; `/api/generate` succeeds or fails per the next item of `loads`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "gemma3:4b"}]})
        ok = loads.pop(0) if loads else True
        if ok:
            return httpx.Response(200, json={"done": True})
        return httpx.Response(500, json={"error": "model requires more system memory"})

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _clean() -> None:
    reset_cache()


async def test_a_model_that_cannot_load_is_not_ready_and_is_retried() -> None:
    # PIP-2: listed on the server, but the warm-up fails (not enough memory)
    transport = server(loads=[False, True])
    first = await local_status(settings(), transport=transport)
    assert first.state == "down"
    assert local_ready(settings()) is False
    # a status poll that does not warm must not turn it ready again
    polled = await local_status(settings(), transport=transport, warm=False)
    assert polled.state == "down" and local_ready(settings()) is False
    # the next refresh retries the load and succeeds
    again = await local_status(settings(), transport=transport)
    assert again.state == "ready" and local_ready(settings()) is True


async def test_a_status_poll_does_not_steal_the_warm_up() -> None:
    # PIP-6: the dashboard's poll sees "ready" first; the refresher must still warm the model
    seen: list[httpx.Request] = []
    transport = server(loads=[], seen=seen)
    await local_status(settings(), transport=transport, warm=False)
    assert not [r for r in seen if r.url.path == "/api/generate"]
    await local_status(settings(), transport=transport)  # the refresher
    assert len([r for r in seen if r.url.path == "/api/generate"]) == 1
    await local_status(settings(), transport=transport)
    assert len([r for r in seen if r.url.path == "/api/generate"]) == 1  # once


async def test_a_server_restart_warms_again() -> None:
    seen: list[httpx.Request] = []
    transport = server(loads=[], seen=seen)
    await local_status(settings(), transport=transport)

    def refuses(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("gone", request=request)

    await local_status(settings(), transport=httpx.MockTransport(refuses))
    await local_status(settings(), transport=transport)
    assert len([r for r in seen if r.url.path == "/api/generate"]) == 2


async def test_mark_unavailable_turns_a_ready_cache_down() -> None:
    await local_status(settings(), transport=server(loads=[]))
    assert local_ready(settings())
    local_mod.mark_unavailable(settings())
    assert local_ready(settings()) is False
    # the next refresh re-checks and warms
    await local_status(settings(), transport=server(loads=[]))
    assert local_ready(settings())
