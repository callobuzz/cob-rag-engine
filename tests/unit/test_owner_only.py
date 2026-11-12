"""Owner-only routes refuse a workspace member's sign-in, and nobody else (accounts A5).

`OWNER_ONLY` (scope_harness.py) is the full list from the A5 spec. A member of tenant A
gets 403 `owner_required` on each of them for A's own resources; an owner's sign-in, the
tenant key and the admin key are not refused for being who they are; a corpus key keeps
exactly what it could do before (its widget keys, its documents).
"""

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.accounts.ratelimit import AccountRateLimiter
from tests.unit.accounts_fakes import FakeRedis
from tests.unit.scope_harness import (
    ADMIN_KEY,
    OWNER_ONLY,
    World,
    call,
    is_owner_only,
    scoped_app,
)

X = str(uuid.uuid4())
Y = str(uuid.uuid4())


def _fill(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format(**ids) if "{" in value else value
    if isinstance(value, dict):
        return {k: _fill(v, ids) for k, v in value.items()}
    return value


def _ids(world: World) -> dict[str, str]:
    return {
        "corpus_id": str(world.corpus_a.id),
        "tenant_id": str(world.tenant_a.id),
        "workspace_id": str(world.workspace_a.id),
        "x": X,
        "y": Y,
    }


async def _run(key: str, method: str, template: str, body: Any, portal: bool) -> Any:
    """The route's answer. The harness database knows little, so a route that got past
    the gate may fail further on: that 500 is an answer too (only 403 is a refusal)."""
    with scoped_app(portal=portal) as (app, world):
        app.state.account_rate_limiter = AccountRateLimiter(client=FakeRedis())
        ids = _ids(world)
        headers = {"X-API-Key": ADMIN_KEY if key == "admin" else key}
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.request(
                method, _fill(template, ids), headers=headers, json=_fill(body, ids)
            )


ROUTES = [pytest.param(*route, id=f"{route[0]} {route[1]}") for route in OWNER_ONLY]


@pytest.mark.parametrize(("method", "template", "body", "portal"), ROUTES)
async def test_member_is_refused(method, template, body, portal) -> None:
    res = await _run("member_a", method, template, body, portal)
    assert res.status_code == 403, res.text
    assert res.json()["code"] == "owner_required"


@pytest.mark.parametrize("key", ["owner_a", "tk_a", "admin", "sa"])
@pytest.mark.parametrize(("method", "template", "body", "portal"), ROUTES)
async def test_owners_and_admin_are_not_refused(key, method, template, body, portal) -> None:
    res = await _run(key, method, template, body, portal)
    # /tenants/me and /billing/* are a workspace's own routes (BIL3: the admin has /admin/billing)
    tenant_self = template.startswith(
        (
            "/api/v1/tenants/me",
            "/api/v1/billing/",
            "/api/v1/onboarding",  # BIZ4
            "/api/v1/value/settings",  # TV4: the owner's own numbers, admin gets no_workspace
        )
    )
    if key in ("admin", "sa") and tenant_self:
        # /tenants/me is a tenant's own route: the admin uses /admin/tenants/{id} instead.
        assert res.status_code == 403 and "owner_required" not in res.text, res.text
        return
    assert res.status_code != 403, res.text
    assert "owner_required" not in res.text


CORPUS_KEY_ROUTES = [
    route
    for route in ROUTES
    if "widget-keys" in route.values[1]
    or route.values[1].startswith(("/api/v1/documents/", "/api/v1/golden/"))
]


@pytest.mark.parametrize(("method", "template", "body", "portal"), CORPUS_KEY_ROUTES)
async def test_corpus_key_keeps_its_powers(method, template, body, portal) -> None:
    res = await _run("ck_a", method, template, body, portal)
    assert res.status_code not in (401, 403), res.text


@pytest.mark.parametrize(
    "body",
    [
        {"prompt_config": {"system_prompt": "Be brief."}},
        {"retrieval_config": {"top_k": 12}},
        {"name": "Renamed", "description": "d"},
    ],
)
async def test_member_edits_prompts_and_retrieval(body) -> None:
    with scoped_app() as (app, world):
        res = await call(app, "member_a", "PUT", f"/api/v1/corpora/{world.corpus_a.id}", json=body)
    assert res.status_code == 200, res.text


@pytest.mark.parametrize("field", ["llm_config", "embedding_config", "tenant_id"])
async def test_member_cannot_change_owner_fields(field) -> None:
    value: Any = {"model": "m"}
    with scoped_app() as (app, world):
        if field == "tenant_id":
            value = str(world.tenant_a.id)
        res = await call(
            app,
            "member_a",
            "PUT",
            f"/api/v1/corpora/{world.corpus_a.id}",
            json={"prompt_config": {"system_prompt": "x"}, field: value},
        )
    assert res.status_code == 403, res.text
    assert res.json() == {
        "detail": f"Only a workspace owner can change {field}",
        "code": "owner_required",
    }


# Routes a member may use on its own workspace's resources (a sample of each kind).
MEMBER_ALLOWED = [
    ("GET", "/api/v1/corpora", None, False),
    ("GET", "/api/v1/corpora/{corpus_id}", None, False),
    ("GET", "/api/v1/corpora/{corpus_id}/widget-keys", None, False),
    ("GET", "/api/v1/documents?corpus_id={corpus_id}", None, False),
    ("PATCH", "/api/v1/config?corpus_id={corpus_id}", {"retrieval_config": {"top_k": 9}}, False),
    ("DELETE", "/api/v1/corpora/{corpus_id}/memories/{x}", None, False),
    ("DELETE", "/api/v1/corpora/{corpus_id}/conversations/{x}", None, False),
    ("GET", "/api/v1/tenants/me", None, False),
    ("GET", "/api/v1/tenants/me/members", None, False),
    ("GET", "/api/v1/portal/workspaces/{workspace_id}", None, True),
    ("PATCH", "/api/v1/portal/workspaces/{workspace_id}", {"title": "Renamed"}, True),
    ("GET", "/api/v1/portal/corpus/{corpus_id}/rules", None, True),
    (
        "POST",
        "/api/v1/portal/corpus/{corpus_id}/rules",
        {"rule_type": "always", "condition": {}, "action": "stage"},
        True,
    ),
]


@pytest.mark.parametrize(("method", "template", "body", "portal"), MEMBER_ALLOWED)
async def test_member_uses_everything_else(method, template, body, portal) -> None:
    assert not is_owner_only(method, template, body)
    res = await _run("member_a", method, template, body, portal)
    assert res.status_code not in (401, 403), res.text
    assert res.status_code < 500, res.text


async def test_member_may_leave() -> None:
    """DELETE /tenants/me/members/{own id} is a member leaving, not an owner action."""
    with scoped_app() as (app, world):
        path = f"/api/v1/tenants/me/members/{world.member_a.id}"
        res = await call(app, "member_a", "DELETE", path)
    assert "owner_required" not in res.text, res.text


def test_owner_only_list_matches_the_spec() -> None:
    """The A5 table: 21 entries (PUT /corpora three times, one per owner field), plus
    version restore and golden-pair delete (A8), plus the ten named-key routes (five on a
    knowledge base's keys, five on the customer's own), plus publish, restore and the on/off
    switch of the assistant (conversation C2), plus deleting a test set and the judge settings (C7), plus applying a guided setup (C8), plus a knowledge base's webhooks, lead delete and export (C4), plus the workspace's transactions (BIL3), plus its statements list, one month and the top-up request (BIL6), plus the website settings, removing a site and leaving a page out (BIZ1), plus the assistant's picture upload (BIZ4), plus making and removing the push link (BIZ2), plus the two first-run routes (BIZ4), plus the value settings and the spend cap (TV4, TV3), plus connecting, configuring and disconnecting a WhatsApp number (TV6), plus the tax details, payments, invoices and add-on purchases (P1, P2, P5), plus the plan list, a plan change and calling one off (P6)."""
    assert len(OWNER_ONLY) == 76
    assert len({(m, t, str(b)) for m, t, b, _ in OWNER_ONLY}) == 76


WEBHOOK_ROUTES = [
    (
        "POST",
        "/api/v1/webhooks",
        {"url": "https://hooks.example/x", "events": ["ingest.completed"]},
    ),
    ("GET", "/api/v1/webhooks", None),
    ("PATCH", "/api/v1/webhooks/{x}", {"enabled": False}),
    ("DELETE", "/api/v1/webhooks/{x}", None),
    ("POST", "/api/v1/webhooks/{x}/test", None),
]


@pytest.mark.parametrize("key", ["owner_a", "member_a", "tk_a", "ck_a"])
@pytest.mark.parametrize(("method", "template", "body"), WEBHOOK_ROUTES)
async def test_webhooks_are_the_operators_alone(key, method, template, body) -> None:
    """Webhook subscriptions are engine-wide (`require_admin`): no workspace principal,
    owner or member, reaches them (A8 checked; nothing to make owner-only)."""
    res = await _run(key, method, template, body, False)
    assert res.status_code == 403, res.text
    assert "owner_required" not in res.text
