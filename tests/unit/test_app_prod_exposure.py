"""What a deployed engine exposes that a dev one may.

Two dev conveniences are dangerous once the process is deployed:

* `/docs`, `/redoc` and `/openapi.json` are in `auth.PUBLIC_PATHS`, so they are
  served with no API key at all. Anyone who can reach the port gets a complete,
  interactive map of every route, parameter and schema.
* `cors_origins` defaults to `["*"]`, which lets any page on the internet call
  the engine from a visitor's browser.

Both were the shipped defaults on every environment, guarded only by the engine
not being published — which is a deployment fact, not a property of the code,
and one that a single stray `ports:` entry undoes. `create_app()` now decides
both from `settings.is_prod`.

These build the app from real environment variables (`get_settings()` reads a
fresh `Settings()` each call) and assert the resulting routes and middleware
directly, rather than trusting a flag was passed.
"""

import pytest
from fastapi.middleware.cors import CORSMiddleware

from callobuzz_rag.api.app import create_app

DOC_PATHS = {"/docs", "/redoc", "/openapi.json"}


def _cors_origins(app) -> list[str]:  # type: ignore[no-untyped-def]
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            return list(middleware.kwargs["allow_origins"])
    raise AssertionError("CORS middleware is not installed at all")


def _paths(app) -> set[str]:  # type: ignore[no-untyped-def]
    return {getattr(route, "path", None) for route in app.routes}


class TestInteractiveDocs:
    def test_dev_serves_them(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The other half of the assertion: if docs were off everywhere, the
        # prod test below would pass while proving nothing.
        monkeypatch.setenv("RAG_ENV", "dev")
        assert _paths(create_app()) >= DOC_PATHS

    @pytest.mark.parametrize("env", ["prod", "production", "PROD"])
    def test_prod_serves_none_of_them(self, monkeypatch: pytest.MonkeyPatch, env: str) -> None:
        monkeypatch.setenv("RAG_ENV", env)
        assert not (DOC_PATHS & _paths(create_app()))


class TestCorsOrigins:
    def test_dev_keeps_the_wildcard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_ENV", "dev")
        monkeypatch.delenv("RAG_CORS_ORIGINS", raising=False)
        assert _cors_origins(create_app()) == ["*"]

    def test_prod_refuses_the_wildcard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_ENV", "prod")
        monkeypatch.delenv("RAG_CORS_ORIGINS", raising=False)
        # No browser calls this engine — its callers are backend services that
        # call it server-side, and none of them sends an Origin.
        assert _cors_origins(create_app()) == []

    def test_prod_honours_an_explicit_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Refusing `*` must not become refusing everything: an operator with a
        # real browser client has to be able to name it.
        monkeypatch.setenv("RAG_ENV", "prod")
        monkeypatch.setenv("RAG_CORS_ORIGINS", '["https://rag-admin.example.com"]')
        assert _cors_origins(create_app()) == ["https://rag-admin.example.com"]
