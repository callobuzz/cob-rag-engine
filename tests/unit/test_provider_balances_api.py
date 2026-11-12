"""BIL1: the super admin's provider balances API (keys, refresh, audit, no leaks)."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.analytics import provider_balances as pb
from callobuzz_rag.api.routes import provider_balances as routes
from callobuzz_rag.core.secrets import generate_fernet_key
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.provider_balance_setting import ProviderBalanceSetting
from tests.unit.test_auth_routes import ADMIN, World, auth_app, req

BASE = "/api/v1/admin/billing/providers"
SECRET_KEY = "sk-admin-SUPERSECRET123"


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    pb.reset_cache()
    routes._last_refresh.clear()
    yield
    pb.reset_cache()
    routes._last_refresh.clear()


@contextmanager
def app_for(world: World, **settings: Any) -> Iterator[Any]:
    # The stand-in database has no SUM(): only the engine-spend query is replaced.
    with (
        auth_app(world, **settings) as app,
        patch.object(pb, "operator_spend_since", AsyncMock(return_value=(2_000_000, 1))),
    ):
        yield app


async def admin(app: Any, method: str, path: str = "", **kw: Any) -> Any:
    return await req(app, method, f"{BASE}{path}", key=ADMIN, **kw)


def rows(world: World) -> list[ProviderBalanceSetting]:
    return world.db.all(ProviderBalanceSetting)


async def test_list_shows_every_provider_not_configured_on_a_fresh_install(world: World) -> None:
    with app_for(world) as app:
        res = await admin(app, "GET")
    assert res.status_code == 200, res.text
    body = res.json()
    assert [i["provider"] for i in body["items"]] == ["openrouter", "openai", "anthropic"]
    assert {i["status"] for i in body["items"]} == {"not_configured"}
    assert body["worst"] is None


async def test_only_the_admin_principal_is_allowed(world: World) -> None:
    with app_for(world) as app:
        no_key = await req(app, "GET", BASE)
        wrong = await req(app, "GET", BASE, key="ck_not_a_key")
        put = await req(app, "PUT", f"{BASE}/openai", key="tk_nope", json={"label": "x"})
    assert no_key.status_code in (401, 403)
    assert wrong.status_code in (401, 403)
    assert put.status_code in (401, 403)


async def test_put_stores_the_key_encrypted_and_never_returns_it(world: World) -> None:
    enc = generate_fernet_key()
    with app_for(world, encryption_key=enc) as app:
        res = await admin(app, "PUT", "/openai", json={"admin_key": SECRET_KEY})
        listed = await admin(app, "GET")
    assert res.status_code == 200, res.text
    assert res.json()["admin_key_set"] is True
    for text in (res.text, listed.text):
        assert "sk-admin" not in text and "SUPERSECRET" not in text
    (row,) = rows(world)
    assert row.admin_key_encrypted and SECRET_KEY not in row.admin_key_encrypted
    audit = [a for a in world.db.all(AuditLog) if a.action == "billing.provider_updated"]
    assert audit[0].details == {
        "provider": "openai",
        "changed": ["label"],
        "key_change": "set",
    }
    assert "SUPERSECRET" not in str(audit[0].details)


async def test_clear_admin_key(world: World) -> None:
    enc = generate_fernet_key()
    with app_for(world, encryption_key=enc) as app:
        await admin(app, "PUT", "/anthropic", json={"admin_key": SECRET_KEY})
        res = await admin(app, "PUT", "/anthropic", json={"clear_admin_key": True})
    assert res.json()["admin_key_set"] is False
    assert rows(world)[0].admin_key_encrypted is None
    last = [a for a in world.db.all(AuditLog) if a.action == "billing.provider_updated"][-1]
    assert last.details["key_change"] == "cleared"


async def test_key_without_an_encryption_key_is_503(world: World) -> None:
    with app_for(world) as app:
        res = await admin(app, "PUT", "/openai", json={"admin_key": SECRET_KEY})
    assert res.status_code == 503
    assert res.json()["code"] == "encryption_not_configured"
    assert rows(world) == []
    assert "SUPERSECRET" not in res.text


async def test_unknown_provider_is_404(world: World) -> None:
    with app_for(world) as app:
        a = await admin(app, "PUT", "/mistral", json={"label": "x"})
        b = await admin(app, "POST", "/manual:00000000-0000-0000-0000-000000000000/refresh")
        c = await admin(app, "PUT", "/manual:not-a-uuid", json={"label": "x"})
    for res in (a, b, c):
        assert res.status_code == 404 and res.json()["code"] == "provider_unknown"


async def test_manual_balance_decimal_string_becomes_micro_and_estimates(world: World) -> None:
    with app_for(world, llm_provider="groq") as app:
        made = await admin(
            app,
            "PUT",
            "/manual:new",
            json={"label": "Groq", "manual_balance_usd": "50.00", "low_threshold_usd": "10"},
        )
    body = made.json()
    assert made.status_code == 200, made.text
    (row,) = rows(world)
    assert row.manual_balance_usd_micro == 50_000_000 and row.low_threshold_usd_micro == 10_000_000
    assert row.manual_balance_at is not None
    assert body["provider"] == "manual" and body["id"] == str(row.id)
    # the patched engine spend (2.00) is subtracted: the label matches RAG_LLM_PROVIDER
    assert body["manual_remaining_usd_micro"] == 48_000_000 and body["status"] == "ok"
    assert body["unpriced_requests"] == 1 and body["spend_tracked"] is True


async def test_manual_new_needs_a_label_and_amounts_are_validated(world: World) -> None:
    with app_for(world) as app:
        no_label = await admin(app, "PUT", "/manual:new", json={"manual_balance_usd": "5"})
        bad = await admin(
            app, "PUT", "/manual:new", json={"label": "x", "manual_balance_usd": "5.1234567"}
        )
        huge = await admin(
            app, "PUT", "/manual:new", json={"label": "x", "manual_balance_usd": "1000000000"}
        )
        both = await admin(
            app,
            "PUT",
            "/manual:new",
            json={"label": "x", "manual_balance_usd": "5", "clear_manual": True},
        )
        extra = await admin(app, "PUT", "/manual:new", json={"label": "x", "surprise": 1})
        key_on_manual = await admin(
            app, "PUT", "/manual:new", json={"label": "x", "admin_key": "k"}
        )
    assert no_label.status_code == 422 and no_label.json()["code"] == "invalid_provider_settings"
    assert bad.status_code == 422 and huge.status_code == 422 and extra.status_code == 422
    assert both.json()["code"] == "invalid_provider_settings"
    assert key_on_manual.json()["code"] == "invalid_provider_settings"
    assert rows(world) == []


async def test_clear_manual_and_note_and_threshold(world: World) -> None:
    with app_for(world) as app:
        made = await admin(
            app,
            "PUT",
            "/manual:new",
            json={
                "label": "Gemini",
                "manual_balance_usd": "20",
                "manual_note": "auto-recharge at $10",
                "low_threshold_usd": "5",
            },
        )
        ident = made.json()["id"]
        res = await admin(
            app,
            "PUT",
            f"/manual:{ident}",
            json={"clear_manual": True, "manual_note": None, "low_threshold_usd": None},
        )
    assert res.status_code == 200, res.text
    (row,) = rows(world)
    assert row.manual_balance_usd_micro is None and row.manual_balance_at is None
    assert row.manual_note is None and row.low_threshold_usd_micro is None
    assert res.json()["status"] == "not_configured"


async def test_refresh_twice_is_429_with_retry_after(world: World) -> None:
    with app_for(world) as app:
        first = await admin(app, "POST", "/openai/refresh")
        second = await admin(app, "POST", "/openai/refresh")
        other = await admin(app, "POST", "/anthropic/refresh")
    assert first.status_code == 200, first.text
    assert second.status_code == 429
    assert second.json()["code"] == "provider_refresh_too_soon"
    assert int(second.headers["retry-after"]) >= 1
    assert other.status_code == 200  # the limit is per provider


async def test_refresh_bypasses_the_cache(world: World) -> None:
    calls: list[bool] = []
    real = pb.provider_balances

    async def spy(*a: Any, **kw: Any) -> Any:
        calls.append(kw.get("refresh", False))
        return await real(*a, **kw)

    with app_for(world) as app, patch.object(routes.pb, "provider_balances", spy):
        await admin(app, "GET")
        await admin(app, "POST", "/openrouter/refresh")
    assert calls == [False, True]


async def test_consumption_and_costs_carry_the_providers(world: World) -> None:
    with app_for(world) as app:
        row = ProviderBalanceSetting(
            provider="manual",
            label="Groq",
            manual_balance_usd_micro=0,
            manual_balance_at=datetime(2026, 10, 1, tzinfo=UTC),
            alert_state="ok",
        )
        world.db.add(row)
        with (
            patch(
                "callobuzz_rag.api.routes.usage.platform_consumption",
                AsyncMock(
                    return_value={
                        "view": "platform",
                        "spend_usd": 1.0,
                        "spend_known": True,
                        "unpriced_requests": 0,
                        "questions": 3,
                        "question_limit": None,
                    }
                ),
            ),
        ):
            res = await req(app, "GET", "/api/v1/usage/consumption", key=ADMIN)
    assert res.status_code == 200, res.text
    providers = res.json()["providers"]
    assert providers["worst"]["label"] == "Groq" and providers["worst"]["status"] == "empty"
    assert [i["label"] for i in providers["items"]] == ["Groq"]


async def test_balance_date_must_carry_a_zone_and_not_be_in_the_future(world: World) -> None:
    with app_for(world, llm_provider="groq") as app:
        naive = await admin(
            app,
            "PUT",
            "/manual:new",
            json={
                "label": "Groq",
                "manual_balance_usd": "5",
                "manual_balance_at": "2026-10-01T00:00:00",
            },
        )
        future = await admin(
            app,
            "PUT",
            "/manual:new",
            json={
                "label": "Groq",
                "manual_balance_usd": "5",
                "manual_balance_at": "2099-01-01T00:00:00Z",
            },
        )
        ok = await admin(
            app,
            "PUT",
            "/manual:new",
            json={
                "label": "Groq",
                "manual_balance_usd": "5",
                "manual_balance_at": "2026-10-01T00:00:00Z",
            },
        )
    assert naive.status_code == 422
    assert future.status_code == 422 and future.json()["code"] == "invalid_provider_settings"
    assert "future" in future.json()["detail"]
    assert ok.status_code == 200, ok.text
    assert len(rows(world)) == 1


async def test_admin_key_source_says_where_the_key_comes_from(world: World) -> None:
    with app_for(
        world, openai_admin_key="sk-from-environment", encryption_key=generate_fernet_key()
    ) as app:
        env = (await admin(app, "GET")).json()
        stored = await admin(app, "PUT", "/anthropic", json={"admin_key": SECRET_KEY})
    by = {i["provider"]: i for i in env["items"]}
    assert by["openai"]["admin_key_set"] is True and by["openai"]["admin_key_source"] == "env"
    assert by["openrouter"]["admin_key_source"] is None
    assert stored.json()["admin_key_source"] == "stored"
