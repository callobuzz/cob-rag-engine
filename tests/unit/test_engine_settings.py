"""GET/PATCH /api/v1/admin/settings and the settings store (accounts A7)."""

from collections.abc import Iterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.accounts import settings_store
from callobuzz_rag.accounts.settings_store import (
    EngineSettings,
    get_engine_settings,
    invalidate_engine_settings_cache,
)
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from tests.unit.scope_harness import KEYS, NOW, World, call, scoped_app


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    invalidate_engine_settings_cache()
    yield
    invalidate_engine_settings_cache()


def _install(world: World, value: dict[str, Any] | None = None) -> EngineSettingsRow:
    """Give the world's session the single engine_settings row."""
    row = EngineSettingsRow(id=1, value=value if value is not None else {}, updated_by=None)
    row.updated_at = NOW
    original = world.session.get.side_effect

    async def get(model: Any, key: Any, *args: Any, **kwargs: Any) -> Any:
        if model is EngineSettingsRow:
            return row if key == 1 else None
        return await original(model, key, *args, **kwargs)

    world.session.get = AsyncMock(side_effect=get)
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    world.session.execute = AsyncMock(return_value=result)
    return row


def _audit_rows(world: World) -> list[AuditLog]:
    return [c.args[0] for c in world.session.add.call_args_list if isinstance(c.args[0], AuditLog)]


async def _patch(app: Any, body: Any, key: str = "admin") -> Any:
    return await call(app, key, "PATCH", "/api/v1/admin/settings", json=body)


# ------------------------------------------------------------------ reads


async def test_defaults_when_the_row_is_empty() -> None:
    with scoped_app() as (app, world):
        _install(world, {})
        res = await call(app, "admin", "GET", "/api/v1/admin/settings")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["signup_mode"] == "approval"
    assert body["branding"] == {
        "product_name": "cob-rag-engine",
        "logo_url": None,
        "primary_color": "#2f8cff",
        "support_email": None,
        "contact_text": "Contact us for pricing.",
        "contact_url": None,
        "hide_powered_by": False,
    }
    assert body["updated_by"] is None
    assert body["updated_at"].startswith("2026-09-30")


def test_stored_values_merge_over_defaults_and_unknown_or_bad_keys_are_ignored() -> None:
    parsed = settings_store._parse_stored(
        {
            "signup_mode": "open",
            "retired_setting": 1,
            "branding": {"product_name": "Sahayak AI", "primary_color": "red", "gone": True},
        }
    )
    assert parsed.signup_mode == "open"
    assert parsed.branding.product_name == "Sahayak AI"
    assert parsed.branding.primary_color == "#2f8cff"  # invalid stored value -> default


# ----------------------------------------------------------------- writes


async def test_patch_signup_mode() -> None:
    with scoped_app() as (app, world):
        row = _install(world)
        res = await _patch(app, {"signup_mode": "open"})
    assert res.status_code == 200, res.text
    assert res.json()["signup_mode"] == "open"
    assert row.value["signup_mode"] == "open"
    assert row.updated_by == "admin key"
    world.session.commit.assert_awaited()
    (audit,) = _audit_rows(world)
    assert audit.action == "settings.updated"
    assert audit.actor_kind == "admin_key"
    assert audit.details == {"changed": {"signup_mode": {"from": "approval", "to": "open"}}}


async def test_patch_branding_merges_with_what_is_stored() -> None:
    with scoped_app() as (app, world):
        row = _install(world, {"branding": {"product_name": "Sahayak AI"}})
        res = await _patch(
            app,
            {"branding": {"primary_color": "#0a7d5a", "support_email": "help@sahayak.example"}},
        )
    assert res.status_code == 200, res.text
    branding = res.json()["branding"]
    assert branding["product_name"] == "Sahayak AI"  # untouched
    assert branding["primary_color"] == "#0a7d5a"
    assert branding["support_email"] == "help@sahayak.example"
    assert row.value["branding"]["product_name"] == "Sahayak AI"
    (audit,) = _audit_rows(world)
    assert set(audit.details["changed"]) == {"branding.primary_color", "branding.support_email"}


async def test_null_resets_a_field_to_its_default() -> None:
    with scoped_app() as (app, world):
        _install(
            world,
            {
                "signup_mode": "closed",
                "branding": {
                    "product_name": "Sahayak AI",
                    "primary_color": "#0a7d5a",
                    "contact_url": "https://sahayak.example/pricing",
                },
            },
        )
        res = await _patch(
            app,
            {"branding": {"product_name": None, "contact_url": None}, "signup_mode": None},
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["signup_mode"] == "approval"
    assert body["branding"]["product_name"] == "cob-rag-engine"
    assert body["branding"]["contact_url"] is None
    assert body["branding"]["primary_color"] == "#0a7d5a"


async def test_branding_null_resets_the_whole_section() -> None:
    with scoped_app() as (app, world):
        _install(world, {"branding": {"product_name": "Sahayak AI", "primary_color": "#0a7d5a"}})
        res = await _patch(app, {"branding": None})
    assert res.status_code == 200, res.text
    assert res.json()["branding"] == EngineSettings().branding.model_dump(mode="json")


@pytest.mark.parametrize(
    "body",
    [{}, {"signup_mode": "approval"}, {"branding": {"product_name": "cob-rag-engine"}}],
)
async def test_a_patch_that_changes_nothing_writes_nothing(body: dict[str, Any]) -> None:
    with scoped_app() as (app, world):
        row = _install(world)
        res = await _patch(app, body)
    assert res.status_code == 200, res.text
    assert row.value == {}
    assert row.updated_by is None
    assert _audit_rows(world) == []
    world.session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "body",
    [
        {"branding": {"primary_color": "blue"}},
        {"branding": {"primary_color": "#12345"}},
        {"branding": {"logo_url": "javascript:alert(1)"}},
        {"branding": {"logo_url": "data:image/png;base64,AAAA"}},
        {"branding": {"logo_url": "/logo.png"}},
        {"branding": {"contact_url": "javascript:alert(1)"}},
        {"branding": {"contact_url": "https://x.example/a b"}},
        {"branding": {"support_email": "not-an-email"}},
        {"branding": {"product_name": ""}},
        {"branding": {"product_name": "x" * 81}},
        {"branding": {"logo_url": "https://x.example/" + "a" * 2048}},
        {"signup_mode": "invite_only"},
        {"unknown": 1},
        {"branding": {"unknown": 1}},
    ],
)
async def test_invalid_values_are_422(body: dict[str, Any]) -> None:
    with scoped_app() as (app, world):
        row = _install(world)
        res = await _patch(app, body)
    assert res.status_code == 422, res.text
    assert row.value == {}


async def test_http_logo_is_refused_in_production_only() -> None:
    body = {"branding": {"logo_url": "http://cdn.example/logo.png"}}
    with scoped_app() as (app, world):
        _install(world)
        res = await _patch(app, body)
    assert res.status_code == 200, res.text  # dev: allowed

    with scoped_app() as (app, world):
        app.state.settings = app.state.settings.model_copy(update={"env": "prod"})
        row = _install(world)
        res = await _patch(app, body)
    assert res.status_code == 422, res.text
    assert res.json()["detail"][0]["loc"] == ["body", "branding", "logo_url"]
    assert row.value == {}

    with scoped_app() as (app, world):
        app.state.settings = app.state.settings.model_copy(update={"env": "prod"})
        _install(world)
        res = await _patch(app, {"branding": {"logo_url": "https://cdn.example/logo.png"}})
    assert res.status_code == 200, res.text


# ------------------------------------------------------------------ access


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("method", ["GET", "PATCH"])
async def test_admin_only(key: str, method: str) -> None:
    with scoped_app() as (app, world):
        _install(world)
        kwargs = {"json": {"signup_mode": "open"}} if method == "PATCH" else {}
        res = await call(app, key, method, "/api/v1/admin/settings", **kwargs)
    if key in ("admin", "sa"):  # the super admin's sign-in is the admin principal
        assert res.status_code == 200, res.text
    else:
        assert res.status_code == 403, res.text
        assert res.json()["detail"] == "Admin access required"


@pytest.mark.parametrize("method", ["GET", "PATCH"])
async def test_no_key_is_401(method: str) -> None:
    with scoped_app() as (app, world):
        _install(world)
        res = await call(app, None, method, "/api/v1/admin/settings", json={})
    assert res.status_code == 401, res.text


# ------------------------------------------------------------------- cache


class _Factory:
    """A session factory counting how often the database is read."""

    def __init__(self, value: dict[str, Any]) -> None:
        self.row = SimpleNamespace(value=value)
        self.reads = 0

    def __call__(self) -> Any:
        factory = self

        @asynccontextmanager
        async def session() -> Any:
            async def get(model: Any, key: Any) -> Any:
                factory.reads += 1
                return factory.row

            yield SimpleNamespace(get=get)

        return session()


async def test_reads_are_cached_and_a_local_write_invalidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _Factory({"signup_mode": "open"})
    clock = [1000.0]
    monkeypatch.setattr(settings_store.time, "monotonic", lambda: clock[0])

    first = await get_engine_settings(factory)
    factory.row.value = {"signup_mode": "closed"}
    second = await get_engine_settings(factory)
    assert first.signup_mode == second.signup_mode == "open"
    assert factory.reads == 1

    clock[0] += 9.9
    await get_engine_settings(factory)
    assert factory.reads == 1  # still inside 10 s

    invalidate_engine_settings_cache()  # what a write in this process does
    assert (await get_engine_settings(factory)).signup_mode == "closed"
    assert factory.reads == 2

    clock[0] += 10.1  # another worker's write shows up within 10 s
    factory.row.value = {"signup_mode": "approval"}
    assert (await get_engine_settings(factory)).signup_mode == "approval"
    assert factory.reads == 3


async def test_a_write_invalidates_the_cache() -> None:
    factory = _Factory({})
    await get_engine_settings(factory)
    assert settings_store._cache is not None
    with scoped_app() as (app, world):
        _install(world)
        res = await _patch(app, {"signup_mode": "closed"})
    assert res.status_code == 200, res.text
    assert settings_store._cache is None


class _RacingFactory(_Factory):
    """A write lands (and invalidates the cache) while this read is in flight."""

    def __call__(self) -> Any:
        invalidate_engine_settings_cache()
        return super().__call__()


async def test_a_read_racing_a_write_does_not_cache_the_old_value() -> None:
    assert (await get_engine_settings(_RacingFactory({"signup_mode": "open"}))).signup_mode == (
        "open"
    )
    assert settings_store._cache is None


async def test_patch_cob_public_sign_in_is_stored_audited_and_uncached() -> None:
    with scoped_app() as (app, world):
        row = _install(world)
        res = await _patch(app, {"cob_public_sign_in": False})
    assert res.status_code == 200, res.text
    assert res.json()["cob_public_sign_in"] is False
    assert row.value["cob_public_sign_in"] is False
    (audit,) = _audit_rows(world)
    assert audit.action == "settings.updated"
    assert audit.details == {"changed": {"cob_public_sign_in": {"from": True, "to": False}}}
    assert settings_store._cache is None  # invalidated after the commit


async def test_cob_public_sign_in_defaults_on_and_null_resets_it() -> None:
    with scoped_app() as (app, world):
        _install(world, {"cob_public_sign_in": False})
        res = await _patch(app, {"cob_public_sign_in": None})
    assert res.json()["cob_public_sign_in"] is True
