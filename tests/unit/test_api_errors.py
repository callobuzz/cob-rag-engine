"""`APIError`: an HTTP error carrying a machine-readable `code` next to `detail`."""

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.api.errors import APIError, error_body, error_response, register_error_handlers


def _app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/coded")
    async def coded() -> None:
        raise APIError(409, "The version is not active", "version_not_active", version="v1")

    @app.get("/headers")
    async def headers() -> None:
        raise APIError(429, "Slow down", "rate_limited", headers={"Retry-After": "7"})

    @app.get("/plain")
    async def plain() -> None:
        raise HTTPException(status_code=404, detail="Nope")

    return app


async def _get(path: str):
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as client:
        return await client.get(path)


async def test_api_error_renders_detail_code_and_extras() -> None:
    res = await _get("/coded")
    assert res.status_code == 409
    assert res.json() == {
        "detail": "The version is not active",
        "code": "version_not_active",
        "version": "v1",
    }


async def test_api_error_keeps_headers() -> None:
    res = await _get("/headers")
    assert res.status_code == 429
    assert res.headers["Retry-After"] == "7"
    assert res.json()["code"] == "rate_limited"


async def test_plain_http_exception_is_unchanged() -> None:
    res = await _get("/plain")
    assert res.status_code == 404
    assert res.json() == {"detail": "Nope"}


def test_error_response_has_the_same_body() -> None:
    res = error_response(503, "Down", "finalize_failed", stage="vector_store")
    assert res.status_code == 503
    assert res.body == (b'{"detail":"Down","code":"finalize_failed","stage":"vector_store"}')
    assert error_body("Down", "x") == {"detail": "Down", "code": "x"}


def test_extras_cannot_shadow_detail_or_code() -> None:
    """A real check, not an `assert` (which `python -O` strips)."""
    import pytest

    with pytest.raises(ValueError, match="code"):
        error_body("d", "c", **{"code": "other"})
    with pytest.raises(ValueError, match="detail"):
        APIError(409, "d", "c", detail="shadow")


def test_code_must_be_a_non_empty_string() -> None:
    import pytest

    with pytest.raises(ValueError, match="code"):
        error_body("d", "")
    with pytest.raises(ValueError, match="code"):
        APIError(409, "d", "")


async def test_handler_re_raises_anything_but_an_api_error() -> None:
    import pytest
    from starlette.requests import Request

    from callobuzz_rag.api.errors import api_error_handler

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    with pytest.raises(RuntimeError, match="boom"):
        await api_error_handler(request, RuntimeError("boom"))


def test_the_engine_app_registers_the_handler() -> None:
    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.api.errors import api_error_handler

    assert create_app().exception_handlers.get(APIError) is api_error_handler
