"""GET /api/v1/public/branding: no credential, only what a login page shows (accounts A7)."""

from collections.abc import Iterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from callobuzz_rag.accounts.settings_store import POWERED_BY, invalidate_engine_settings_cache
from tests.unit.scope_harness import call, scoped_app

PUBLIC_KEYS = {
    "product_name",
    "logo_url",
    "primary_color",
    "support_email",
    "contact_text",
    "contact_url",
    "signup_mode",
    "powered_by",
    "accounts_enabled",
    "password_policy",
    "signup_available",
    "special_use_emails_allowed",
    "cob_public",
    "demo",  # L5a demo
    "legal",  # P3 legal
    "landing",  # LP1 landing
}


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    invalidate_engine_settings_cache()
    yield
    invalidate_engine_settings_cache()


def _factory(value: dict[str, Any]) -> Any:
    row = SimpleNamespace(value=value, updated_by="owner@operator.example")

    def factory() -> Any:
        @asynccontextmanager
        async def session() -> Any:
            async def get(model: Any, key: Any) -> Any:
                return row

            yield SimpleNamespace(get=get)

        return session()

    return factory


async def _branding(stored: dict[str, Any], key: str | None = None) -> Any:
    with scoped_app() as (app, _):
        app.state.session_factory = _factory(stored)
        return await call(app, key, "GET", "/api/v1/public/branding")


async def test_no_key_gets_the_defaults_and_exactly_the_listed_keys() -> None:
    res = await _branding({})
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == PUBLIC_KEYS  # anything new here must be added on purpose
    assert body["product_name"] == "cob-rag-engine"
    assert body["signup_mode"] == "approval"
    assert body["powered_by"] == {**POWERED_BY, "url": None}
    assert res.headers["cache-control"] == "public, max-age=60"
    assert body["accounts_enabled"] is False  # no RAG_SESSION_SECRET in the test settings
    assert body["password_policy"] == {"min_length": 10, "max_length": 128}


async def test_accounts_enabled_with_a_session_secret() -> None:
    with scoped_app() as (app, _):
        app.state.session_factory = _factory({})
        app.state.settings = app.state.settings.model_copy(
            update={"session_secret": SecretStr("s" * 40)}
        )
        res = await call(app, None, "GET", "/api/v1/public/branding")
    assert res.status_code == 200, res.text
    assert res.json()["accounts_enabled"] is True


async def _available(stored: dict[str, Any], **settings: Any) -> bool:
    invalidate_engine_settings_cache()
    with scoped_app() as (app, _):
        app.state.session_factory = _factory(stored)
        app.state.settings = app.state.settings.model_copy(update=settings)
        res = await call(app, None, "GET", "/api/v1/public/branding")
    assert res.status_code == 200, res.text
    return bool(res.json()["signup_available"])


async def test_signup_available() -> None:
    on = {"session_secret": SecretStr("s" * 40), "email_provider": "log"}
    assert await _available({}, **on) is True  # approval (default)
    assert await _available({"signup_mode": "open"}, **on) is True
    assert await _available({"signup_mode": "closed"}, **on) is False
    assert await _available({}, **{**on, "email_provider": "disabled"}) is False
    assert await _available({}, email_provider="log") is False  # accounts not configured


@pytest.mark.parametrize(
    ("env", "allowed"), [("dev", True), ("prod", False), ("production", False)]
)
async def test_special_use_emails_follow_the_environment(env: str, allowed: bool) -> None:
    with scoped_app() as (app, _):
        app.state.session_factory = _factory({})
        app.state.settings = app.state.settings.model_copy(update={"env": env})
        res = await call(app, None, "GET", "/api/v1/public/branding")
    assert res.status_code == 200, res.text
    assert res.json()["special_use_emails_allowed"] is allowed


async def test_bad_key_is_ignored_on_a_public_route() -> None:
    with scoped_app() as (app, _):
        app.state.session_factory = _factory({})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            res = await c.get("/api/v1/public/branding", headers={"X-API-Key": "nonsense"})
    assert res.status_code == 200, res.text


async def test_powered_by_is_constant_whatever_the_stored_branding() -> None:
    stored = {
        "signup_mode": "closed",
        "powered_by": {"text": "Powered by Sahayak", "url": "https://sahayak.example"},
        "branding": {
            "product_name": "Sahayak AI",
            "primary_color": "#0a7d5a",
            "powered_by": {"text": "nope"},
        },
    }
    res = await _branding(stored)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["product_name"] == "Sahayak AI"
    assert body["primary_color"] == "#0a7d5a"
    assert body["signup_mode"] == "closed"
    assert body["powered_by"] == {"text": "cob-rag-engine by Callobuzz", "url": None}
    assert set(body) == PUBLIC_KEYS
    assert "updated_by" not in res.text and "owner@operator.example" not in res.text


@pytest.mark.parametrize(
    "body",
    [
        {"powered_by": {"text": "x"}},
        {"branding": {"powered_by": {"text": "x"}}},
        {"branding": {"show_powered_by": False}},
    ],
)
async def test_powered_by_cannot_be_set(body: dict[str, Any]) -> None:
    with scoped_app() as (app, _):
        res = await call(app, "admin", "PATCH", "/api/v1/admin/settings", json=body)
    assert res.status_code == 422, res.text


def test_openapi_marks_public_routes_open_and_widget_routes_widget_key() -> None:
    with scoped_app() as (app, _):
        schema = app.openapi()
    paths = schema["paths"]
    assert paths["/api/v1/public/branding"]["get"]["security"] == []
    assert paths["/api/v1/widget/config"]["get"]["security"] == [{"WidgetKey": []}]
    assert paths["/api/v1/widget/chat"]["post"]["security"] == [{"WidgetKey": []}]
    both = [{"ApiKey": []}, {"BearerAuth": []}]
    assert paths["/api/v1/admin/settings"]["get"]["security"] == both
    assert paths["/api/v1/admin/settings"]["patch"]["security"] == both


async def _cob_public(stored: dict[str, Any], **settings: Any) -> bool:
    invalidate_engine_settings_cache()
    with scoped_app() as (app, _):
        app.state.session_factory = _factory(stored)
        app.state.settings = app.state.settings.model_copy(update=settings)
        res = await call(app, None, "GET", "/api/v1/public/branding")
    assert res.status_code == 200, res.text
    return bool(res.json()["cob_public"])


async def test_cob_public_needs_every_switch() -> None:
    assert await _cob_public({}) is True  # on by default
    assert await _cob_public({"cob_public_sign_in": False}) is False
    assert await _cob_public({}, cob_public_enabled=False) is False
    assert await _cob_public({}, cob_enabled=False) is False


async def test_the_public_website_is_off_by_default() -> None:  # LP1 landing
    res = await _branding({})
    assert res.status_code == 200
    assert res.json()["landing"] == {"enabled": False}
