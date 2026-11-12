import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.core.url_guard import UnsafeURLError
from callobuzz_rag.db.models.audit_log import AuditLog
from tests.unit.scope_harness import MATRIX, call, check, expected, scoped_app

BODY = {
    "url": "https://crm.shree-labs.in/hooks/leads",
    "events": ["lead.created"],
    "secret": "s" * 24,
}
GUARD = "callobuzz_rag.api.routes.corpus_webhooks.acheck_public_https_url"


@pytest.mark.parametrize(("key", "which", "outcome"), MATRIX)
async def test_list_scoped(key: str | None, which: str, outcome: str) -> None:
    with scoped_app() as (app, world):
        url = f"/api/v1/corpora/{world.corpus(which).id}/webhooks"
        res = await call(app, key, "GET", url)
    check(expected(key, outcome, "GET", url), res.status_code, res.text)


async def test_member_cannot_create_owner_can() -> None:
    with scoped_app() as (app, world), patch(GUARD, AsyncMock()):
        url = f"/api/v1/corpora/{world.corpus('a').id}/webhooks"
        res = await call(app, "member_a", "POST", url, json=BODY)
        assert res.status_code == 403 and res.json()["code"] == "owner_required"
        res = await call(app, "owner_a", "POST", url, json=BODY)
        assert res.status_code == 201, res.text
        assert res.json()["corpus_id"] == str(world.corpus("a").id)
        assert "secret" not in res.json()
        added = [c.args[0] for c in world.session.add.call_args_list]
        assert any(isinstance(o, AuditLog) and o.action == "corpus_webhook.created" for o in added)


@pytest.mark.parametrize(
    "body",
    [
        {**BODY, "url": "http://crm.shree-labs.in/hook"},
        {**BODY, "events": ["config.changed"]},
        {**BODY, "events": []},
        {**BODY, "secret": "short"},
        {**BODY, "enabled": True},
    ],
)
async def test_bad_bodies(body: dict[str, Any]) -> None:
    with scoped_app() as (app, world), patch(GUARD, AsyncMock()):
        res = await call(
            app, "owner_a", "POST", f"/api/v1/corpora/{world.corpus('a').id}/webhooks", json=body
        )
    assert res.status_code == 422


async def test_an_address_inside_the_network_is_refused() -> None:
    with scoped_app() as (app, world), patch(GUARD, AsyncMock(side_effect=UnsafeURLError("x"))):
        res = await call(
            app,
            "owner_a",
            "POST",
            f"/api/v1/corpora/{world.corpus('a').id}/webhooks",
            json={**BODY, "url": "https://169.254.169.254/latest"},
        )
    assert res.status_code == 422 and res.json()["code"] == "webhook_url_not_allowed"


async def test_a_webhook_of_another_knowledge_base_is_not_found() -> None:
    with scoped_app() as (app, world):
        url = f"/api/v1/corpora/{world.corpus('a').id}/webhooks/{uuid.uuid4()}"
        res = await call(app, "owner_a", "PATCH", url, json={"enabled": False})
        assert res.status_code == 404 and res.json()["code"] == "webhook_not_found"
        res = await call(app, "owner_a", "DELETE", url)
        assert res.status_code == 404


async def test_engine_wide_webhook_refuses_lead_events() -> None:
    with scoped_app() as (app, _):
        res = await call(
            app, "admin", "POST", "/api/v1/webhooks", json={**BODY, "events": ["lead.created"]}
        )
    assert res.status_code == 422 and res.json()["code"] == "webhook_event_not_allowed"
    assert res.json()["events"] == ["lead.created"]
    with scoped_app() as (app, _):
        res = await call(
            app,
            "admin",
            "POST",
            "/api/v1/webhooks",
            json={**BODY, "events": ["handoff.requested", "config.changed"]},
        )
    assert res.status_code == 422 and res.json()["events"] == ["handoff.requested"]
