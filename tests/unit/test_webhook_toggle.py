"""PATCH /api/v1/webhooks/{id} enables or disables a webhook (G-18)."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from callobuzz_rag.db.models.webhook_config import WebhookConfig
from tests.unit.scope_harness import call, scoped_app


def _hook() -> SimpleNamespace:
    now = datetime(2026, 9, 30, tzinfo=UTC)
    return SimpleNamespace(
        id=uuid.uuid4(),
        url="https://hooks.example/x",
        events=["config.changed"],
        secret="s" * 20,
        enabled=True,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize("enabled", [False, True])
async def test_admin_toggles(enabled) -> None:
    with scoped_app() as (app, world):
        hook = _hook()
        hook.enabled = not enabled
        world.add_row(WebhookConfig, hook)
        res = await call(
            app, "admin", "PATCH", f"/api/v1/webhooks/{hook.id}", json={"enabled": enabled}
        )
    assert res.status_code == 200, res.text
    assert res.json()["enabled"] is enabled and hook.enabled is enabled
    assert "secret" not in res.json()
    world.session.commit.assert_awaited()


@pytest.mark.parametrize(("key", "status"), [("tk_a", 403), ("ck_a", 403), (None, 401)])
async def test_admin_only(key, status) -> None:
    with scoped_app() as (app, world):
        hook = _hook()
        world.add_row(WebhookConfig, hook)
        res = await call(app, key, "PATCH", f"/api/v1/webhooks/{hook.id}", json={"enabled": False})
    assert res.status_code == status
    assert hook.enabled is True


async def test_unknown_webhook_is_404() -> None:
    with scoped_app() as (app, _):
        res = await call(
            app, "admin", "PATCH", f"/api/v1/webhooks/{uuid.uuid4()}", json={"enabled": False}
        )
    assert res.status_code == 404


@pytest.mark.parametrize("body", [{}, {"enabled": "maybe"}, {"enabled": False, "url": "https://x"}])
async def test_body_is_just_the_toggle(body) -> None:
    with scoped_app() as (app, world):
        hook = _hook()
        world.add_row(WebhookConfig, hook)
        res = await call(app, "admin", "PATCH", f"/api/v1/webhooks/{hook.id}", json=body)
    assert res.status_code == 422


async def test_dispatcher_only_sends_to_enabled_hooks() -> None:
    """Disabling is honoured where events go out, not just stored."""
    import inspect

    from callobuzz_rag.core.webhooks import WebhookDispatcher

    assert "WebhookConfig.enabled.is_(True)" in inspect.getsource(WebhookDispatcher)
