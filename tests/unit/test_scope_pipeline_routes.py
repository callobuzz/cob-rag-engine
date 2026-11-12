"""Pipeline profile routes and scoping (PIP1): every admin route is for the admin principal
only. A tenant key, a knowledge-base key and a workspace session (owner or member) are refused,
and no credential at all is a 401. PIP3 extends this file for the fair-use routes."""

import uuid
from typing import Any

import pytest

from tests.unit.scope_harness import call, scoped_app

PID = uuid.uuid4()
ADMIN_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/v1/admin/pipelines", None),
    ("POST", "/api/v1/admin/pipelines", {"name": "Spare", "label": "Spare"}),
    ("GET", "/api/v1/admin/pipelines/lanes", None),
    ("GET", "/api/v1/admin/pipelines/tenants/{tenant_id}", None),
    ("PUT", "/api/v1/admin/pipelines/tenants/{tenant_id}", {"profile_id": None}),
    ("GET", f"/api/v1/admin/pipelines/{PID}", None),
    ("PATCH", f"/api/v1/admin/pipelines/{PID}", {"label": "x"}),
    ("DELETE", f"/api/v1/admin/pipelines/{PID}", None),
    ("POST", f"/api/v1/admin/pipelines/{PID}/clone", {"name": "Copy"}),
    ("POST", f"/api/v1/admin/pipelines/{PID}/default", None),
]


@pytest.mark.parametrize("key", ["tk_a", "tk_b", "ck_a", "ck_b", "owner_a", "member_a"])
@pytest.mark.parametrize(("method", "template", "body"), ADMIN_ROUTES)
async def test_every_workspace_credential_is_refused(
    key: str, method: str, template: str, body: Any
) -> None:
    with scoped_app() as (app, world):
        path = template.format(tenant_id=world.tenant_a.id)
        res = await call(app, key, method, path, json=body)
    assert res.status_code == 403, (key, method, path, res.text)


@pytest.mark.parametrize(("method", "template", "body"), ADMIN_ROUTES)
async def test_no_credential_is_401(method: str, template: str, body: Any) -> None:
    with scoped_app() as (app, world):
        res = await call(app, None, method, template.format(tenant_id=world.tenant_a.id), json=body)
    assert res.status_code == 401


@pytest.mark.parametrize("key", ["admin", "sa"])
async def test_the_admin_principal_reads_the_catalogue(key: str) -> None:
    with scoped_app() as (app, _):
        res = await call(app, key, "GET", "/api/v1/admin/pipelines")
    assert res.status_code == 200, res.text
    assert res.json() == {"items": [], "default_id": None}  # the stand-in database is empty


@pytest.mark.parametrize("key", ["tk_a", "owner_a", "member_a", "ck_a"])
async def test_a_workspace_cannot_set_another_workspaces_profile(key: str) -> None:
    with scoped_app() as (app, world):
        res = await call(
            app,
            key,
            "PUT",
            f"/api/v1/admin/pipelines/tenants/{world.tenant_b.id}",
            json={"profile_id": None},
        )
    assert res.status_code == 403


# PIP3 fair use: the deployer's routes are for the admin principal only; the owner route is for
# a workspace (a `ck_` key and the admin principal are refused there, see test_fair_use_routes.py)
FAIR_USE_ADMIN_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/v1/admin/fair-use", None),
    ("GET", "/api/v1/admin/fair-use/tenants/{tenant_id}", None),
    ("PUT", "/api/v1/admin/fair-use/tenants/{tenant_id}", {"limits": {"storage_mb": 5}}),
]


@pytest.mark.parametrize("key", ["tk_a", "tk_b", "ck_a", "ck_b", "owner_a", "member_a"])
@pytest.mark.parametrize(("method", "template", "body"), FAIR_USE_ADMIN_ROUTES)
async def test_every_workspace_credential_is_refused_on_the_fair_use_admin_routes(
    key: str, method: str, template: str, body: Any
) -> None:
    with scoped_app() as (app, world):
        path = template.format(tenant_id=world.tenant_a.id)
        res = await call(app, key, method, path, json=body)
    assert res.status_code == 403, (key, method, path, res.text)


@pytest.mark.parametrize(("method", "template", "body"), FAIR_USE_ADMIN_ROUTES)
async def test_no_credential_is_401_on_the_fair_use_admin_routes(
    method: str, template: str, body: Any
) -> None:
    with scoped_app() as (app, world):
        res = await call(app, None, method, template.format(tenant_id=world.tenant_a.id), json=body)
    assert res.status_code == 401


@pytest.mark.parametrize("key", ["tk_a", "owner_a", "member_a", "ck_a"])
async def test_a_workspace_cannot_set_another_workspaces_fair_use(key: str) -> None:
    with scoped_app() as (app, world):
        res = await call(
            app,
            key,
            "PUT",
            f"/api/v1/admin/fair-use/tenants/{world.tenant_b.id}",
            json={"limits": {"storage_mb": 5}},
        )
    assert res.status_code == 403
# /PIP3 fair use
