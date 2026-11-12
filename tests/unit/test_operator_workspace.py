"""The operator's own workspace and the Internal and Pilot plans.

Bootstrap creates it (idempotently), the lifecycle protects it, the plan rules keep the
Internal plan to it, and the super admin works inside it through `switch-tenant` without
ever getting an admin scope there. Through the real app and middleware on the in-memory
database of `accounts_fakes.py`; `tests/integration/test_operator_workspace_db.py` runs the
migration and the bootstrap on PostgreSQL.
"""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.accounts import bootstrap, operator, plans
from callobuzz_rag.accounts.tokens import decode_access_token
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User
from tests.unit.test_auth_routes import ADMIN, SA_EMAIL, SECRET, World, auth_app, login, req
from tests.unit.test_plans_admin import add_plan, app_for
from tests.unit.test_tenant_lifecycle import admin, admin_app


@pytest.fixture
def world() -> World:
    return World()


def _settings(email: str = SA_EMAIL) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        session_secret=SECRET,  # type: ignore[arg-type]
        superadmin_email=email,
        email_provider="log",
    )


async def _bootstrap(world: World, email: str = SA_EMAIL, **kw: Any) -> int:
    return await bootstrap.run(_settings(email), world.db.factory(), out=lambda _l: None, **kw)


def _operator(world: World) -> Tenant:
    (tenant,) = [t for t in world.db.all(Tenant) if t.is_operator]
    return tenant


def _owners(world: World, tenant: Tenant) -> list[str]:
    ids = {m.user_id for m in world.db.all(TenantMember) if m.tenant_id == tenant.id}
    return sorted(u.email for u in world.db.all(User) if u.id in ids)


def _actions(world: World, action: str) -> list[AuditLog]:
    return [a for a in world.db.all(AuditLog) if a.action == action]


def _stored_product_name(world: World, name: str) -> None:
    world.db.add(EngineSettingsRow(id=1, value={"branding": {"product_name": name}}))


# ---------------------------------------------------------------- bootstrap


async def test_bootstrap_creates_the_workspace_owned_by_the_super_admin(world: World) -> None:
    _stored_product_name(world, "Sahayak AI")
    assert await _bootstrap(world) == 0
    tenant = _operator(world)
    internal = next(p for p in world.db.all(Plan) if p.is_internal)
    assert tenant.name == "Sahayak AI" and tenant.status == "active"
    assert tenant.plan_id == internal.id and tenant.activated_at is not None
    assert _owners(world, tenant) == [SA_EMAIL]
    (member,) = [m for m in world.db.all(TenantMember) if m.tenant_id == tenant.id]
    assert member.role == "owner"
    assert (internal.max_knowledge_bases, internal.monthly_questions) == (None, None)
    assert (internal.max_documents, internal.max_widget_keys) == (None, None)
    (row,) = _actions(world, "operator_workspace.created")
    assert row.tenant_id == tenant.id and row.details["owner"] == SA_EMAIL


async def test_the_name_falls_back_when_no_product_name_is_stored(world: World) -> None:
    await _bootstrap(world)
    assert _operator(world).name == "My workspace"


async def test_bootstrap_twice_changes_nothing(world: World) -> None:
    await _bootstrap(world)
    first = (_operator(world).id, len(world.db.all(TenantMember)), len(world.db.all(AuditLog)))
    sa = next(u for u in world.db.all(User) if u.email == SA_EMAIL)
    sa.password_hash = "h"  # the next starts take the "ready" path
    before = len(world.db.all(AuditLog))
    assert await _bootstrap(world) == 0
    assert await _bootstrap(world) == 0
    assert len([t for t in world.db.all(Tenant) if t.is_operator]) == 1
    assert (_operator(world).id, len(world.db.all(TenantMember))) == first[:2]
    assert len(_actions(world, "operator_workspace.created")) == 1
    assert len(world.db.all(AuditLog)) == before
    assert len([p for p in world.db.all(Plan) if p.is_internal]) == 1


async def test_a_super_admin_in_the_workspace_is_not_a_customer_member(world: World) -> None:
    await _bootstrap(world)
    sa = next(u for u in world.db.all(User) if u.email == SA_EMAIL)
    sa.password_hash = "h"
    assert await _bootstrap(world) == bootstrap.EXIT_OK  # not "belongs to a workspace"


async def test_no_super_admin_address_still_creates_an_empty_workspace(world: World) -> None:
    code = await bootstrap.run(_settings(""), world.db.factory(), out=lambda _l: None)
    assert code == bootstrap.EXIT_OK
    tenant = _operator(world)
    assert _owners(world, tenant) == [] and tenant.status == "active"
    await bootstrap.run(_settings(""), world.db.factory(), out=lambda _l: None)
    assert len([t for t in world.db.all(Tenant) if t.is_operator]) == 1


async def test_a_new_super_admin_owns_the_workspace_and_the_old_one_is_taken_out(
    world: World,
) -> None:
    await _bootstrap(world)
    tenant = _operator(world)
    await _bootstrap(world, "new-ops@operator.example")
    assert _operator(world).id == tenant.id  # the same workspace, not a second one
    assert _owners(world, tenant) == ["new-ops@operator.example"]
    old = next(u for u in world.db.all(User) if u.email == SA_EMAIL)
    assert old.is_superadmin is False


async def test_deleting_the_workspace_on_purpose_is_remembered(world: World) -> None:
    await _bootstrap(world)
    tenant = _operator(world)
    async with world.db.factory()() as session:
        await operator.set_workspace_removed(session, True)
        await session.delete(tenant)
    await _bootstrap(world)
    assert [t for t in world.db.all(Tenant) if t.is_operator] == []
    await _bootstrap(world, restore_operator_workspace=True)
    assert _owners(world, _operator(world)) == [SA_EMAIL]
    await _bootstrap(world)
    assert len([t for t in world.db.all(Tenant) if t.is_operator]) == 1


# --------------------------------------------------------------- protection


async def _with_workspace(world: World) -> Tenant:
    await _bootstrap(world)
    return _operator(world)


async def test_the_workspace_cannot_be_suspended(world: World) -> None:
    tenant = await _with_workspace(world)
    with admin_app(world) as (app, _):
        via_route = await admin(
            app, "POST", f"/{tenant.id}/suspend", json={"confirm_name": tenant.name}
        )
        via_put = await admin(
            app, "PUT", f"/{tenant.id}", json={"is_active": False, "confirm_name": tenant.name}
        )
    for res in (via_route, via_put):
        assert res.status_code == 409, res.text
        assert res.json()["code"] == "operator_workspace_protected"
    assert tenant.status == "active"
    assert len(_actions(world, "operator_workspace.suspend_refused")) == 2
    assert world.db.commits >= 2  # the audit rows are kept although the request failed


async def test_deleting_it_needs_the_name_and_the_extra_flag(world: World) -> None:
    tenant = await _with_workspace(world)
    path = f"/{tenant.id}"
    with admin_app(world) as (app, _):
        no_name = await admin(app, "DELETE", path, params={"confirm_operator_workspace": "true"})
        wrong = await admin(
            app,
            "DELETE",
            path,
            params={"confirm_name": "nope", "confirm_operator_workspace": "true"},
        )
        no_flag = await admin(app, "DELETE", path, params={"confirm_name": tenant.name})
        assert _operator(world).id == tenant.id  # nothing was deleted yet
        done = await admin(
            app,
            "DELETE",
            path,
            params={"confirm_name": tenant.name, "confirm_operator_workspace": "true"},
        )
    assert no_name.status_code == 422
    assert wrong.status_code == 422 and wrong.json()["code"] == "confirm_mismatch"
    assert no_flag.status_code == 422
    assert no_flag.json()["code"] == "operator_confirmation_required"
    assert done.status_code == 204, done.text
    assert [t for t in world.db.all(Tenant) if t.is_operator] == []
    (refused,) = _actions(world, "operator_workspace.delete_refused")
    assert refused.tenant_id == tenant.id
    (deleted,) = _actions(world, "tenant.deleted")
    assert deleted.details["operator_workspace"] is True
    # the super admin account survives, and the next start does not recreate it
    assert any(u.email == SA_EMAIL for u in world.db.all(User))
    await _bootstrap(world)
    assert [t for t in world.db.all(Tenant) if t.is_operator] == []


async def test_a_customer_deletes_without_the_extra_flag(world: World) -> None:
    with admin_app(world) as (app, _):
        res = await admin(
            app, "DELETE", f"/{world.shree.id}", params={"confirm_name": world.shree.name}
        )
    assert res.status_code == 204


async def test_the_marker_cannot_be_set_through_the_api(world: World) -> None:
    tenant = await _with_workspace(world)
    with admin_app(world) as (app, _):
        res = await admin(app, "PUT", f"/{tenant.id}", json={"is_operator": False})
        made = await admin(app, "POST", "", json={"name": "Sneaky", "is_operator": True})
    assert res.status_code in (200, 422) and tenant.is_operator is True
    assert made.status_code in (201, 422)
    assert len([t for t in world.db.all(Tenant) if t.is_operator]) == 1


# -------------------------------------------------------------------- plans


async def _internal(world: World) -> Plan:
    await _with_workspace(world)
    return next(p for p in world.db.all(Plan) if p.is_internal)


async def test_only_the_operator_workspace_may_use_the_internal_plan(world: World) -> None:
    internal = await _internal(world)
    operator_tenant = _operator(world)
    other = add_plan(world, "Pro", max_documents=10)
    with admin_app(world) as (app, _):
        refused = await admin(
            app, "POST", f"/{world.shree.id}/plan", json={"plan_id": str(internal.id)}
        )
        created = await admin(app, "POST", "", json={"name": "Kaveri", "plan_id": str(internal.id)})
        moved = await admin(
            app, "POST", f"/{operator_tenant.id}/plan", json={"plan_id": str(other.id)}
        )
        back = await admin(
            app, "POST", f"/{operator_tenant.id}/plan", json={"plan_id": str(internal.id)}
        )
    for res in (refused, created):
        assert res.status_code == 409 and res.json()["code"] == "plan_not_assignable"
    assert world.shree.plan_id != internal.id
    assert moved.status_code == 200 and back.status_code == 200
    assert operator_tenant.plan_id == internal.id


async def test_activating_a_signup_on_the_internal_plan_is_refused(world: World) -> None:
    internal = await _internal(world)
    world.shree.status = "pending"
    with admin_app(world) as (app, _):
        res = await admin(
            app, "POST", f"/{world.shree.id}/activate", json={"plan_id": str(internal.id)}
        )
    assert res.status_code == 409 and res.json()["code"] == "plan_not_assignable"
    assert world.shree.status == "pending"


async def test_the_internal_plan_is_locked_and_hidden_from_the_picker(world: World) -> None:
    internal = await _internal(world)
    with app_for(world) as app:
        listed = await req(app, "GET", "/api/v1/admin/plans", key=ADMIN)
        picker = await req(
            app, "GET", "/api/v1/admin/plans", key=ADMIN, params={"include_inactive": "false"}
        )
        edit = await req(
            app, "PATCH", f"/api/v1/admin/plans/{internal.id}", key=ADMIN, json={"name": "X"}
        )
        gone = await req(app, "DELETE", f"/api/v1/admin/plans/{internal.id}", key=ADMIN)
    by_name = {p["name"]: p for p in listed.json()["items"]}
    assert by_name["Internal"]["is_internal"] is True
    assert by_name["Standard"]["is_internal"] is False
    assert "Internal" not in {p["name"] for p in picker.json()["items"]}
    for res in (edit, gone):
        assert res.status_code == 409 and res.json()["code"] == "plan_internal_locked"
    assert internal.name == "Internal"


async def test_the_api_cannot_create_an_internal_plan(world: World) -> None:
    with app_for(world) as app:
        res = await req(
            app,
            "POST",
            "/api/v1/admin/plans",
            key=ADMIN,
            json={"name": "Mine", "is_internal": True},
        )
    assert res.status_code == 422


async def test_pilot_style_plans_stay_editable_and_deletable(world: World) -> None:
    pilot = add_plan(
        world,
        "Pilot",
        max_knowledge_bases=1,
        monthly_questions=300,
        max_documents=50,
        max_widget_keys=1,
    )
    assert pilot.is_builtin is False and pilot.is_internal is False
    with app_for(world) as app:
        edit = await req(
            app,
            "PATCH",
            f"/api/v1/admin/plans/{pilot.id}",
            key=ADMIN,
            json={"monthly_questions": 500},
        )
        gone = await req(app, "DELETE", f"/api/v1/admin/plans/{pilot.id}", key=ADMIN)
    assert edit.status_code == 200 and edit.json()["monthly_questions"] == 500
    assert gone.status_code == 204


# ------------------------------------------------- the super admin inside it


def _claims(app: Any, token: str) -> Any:
    return decode_access_token(app.state.settings, token)


async def _super_admin_in(world: World) -> User:
    sa = world.user(SA_EMAIL, superadmin=True)
    async with world.db.factory()() as session:
        await operator.ensure_operator_workspace(session, owner=sa)
    return sa


async def _post(app: Any, path: str, token: str, **kw: Any) -> Any:
    return await req(app, "POST", f"/api/v1/auth/{path}", token=token, **kw)


async def test_switching_into_and_out_of_the_workspace(world: World) -> None:
    sa = await _super_admin_in(world)
    tenant = _operator(world)
    with (
        auth_app(world) as app,
        patch.object(plans, "tenant_counts", AsyncMock(return_value={})),
    ):
        signed = await login(app, SA_EMAIL)
        body = signed.json()
        assert body["tenant"] is None
        assert body["tenants"] == [
            {
                "id": str(tenant.id),
                "name": "My workspace",
                "role": "owner",
                "status": "active",
                "is_operator": True,
            }
        ]
        admin_who = await req(app, "GET", "/api/v1/whoami", token=body["access_token"])
        entered = await _post(
            app, "switch-tenant", body["access_token"], json={"tenant_id": str(tenant.id)}
        )
        inside = entered.json()["access_token"]
        who = await req(app, "GET", "/api/v1/whoami", token=inside)
        plans_in = await req(app, "GET", "/api/v1/admin/plans", token=inside)
        refused = await _post(app, "switch-tenant", inside, json={"tenant_id": str(world.shree.id)})
        refreshed = await req(
            app, "POST", "/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]}
        )
        left = await _post(app, "switch-admin", inside)
        outside = left.json()["access_token"]
        again = await req(app, "GET", "/api/v1/whoami", token=outside)
        plans_out = await req(app, "GET", "/api/v1/admin/plans", token=outside)
        again_left = await _post(app, "switch-admin", outside)
    assert admin_who.json()["kind"] == "admin"
    assert entered.status_code == 200 and entered.json()["tenant"]["is_operator"] is True
    assert _claims(app, inside).tenant_id == tenant.id
    # inside: a plain tenant scope with the owner role; the admin routes are closed
    assert who.json()["kind"] == "tenant" and who.json()["tenant_id"] == str(tenant.id)
    assert who.json()["role"] == "owner"
    assert plans_in.status_code == 403
    # no other tenant is reachable from the super admin, in or out of the workspace
    assert refused.status_code == 403 and refused.json()["code"] == "not_member"
    # a refresh keeps the workspace the session is in
    assert refreshed.status_code == 200
    assert refreshed.json()["tenant"]["id"] == str(tenant.id)
    assert _claims(app, refreshed.json()["access_token"]).tenant_id == tenant.id
    # back: the admin scope again
    assert left.status_code == 200 and left.json()["tenant"] is None
    assert _claims(app, outside).tenant_id is None
    assert again.json()["kind"] == "admin" and plans_out.status_code == 200
    assert again_left.status_code == 200
    assert world.sessions(sa)[0].tenant_id is None


async def test_login_can_open_straight_into_the_workspace(world: World) -> None:
    await _super_admin_in(world)
    tenant = _operator(world)
    with auth_app(world) as app:
        inside = await login(app, SA_EMAIL, tenant_id=str(tenant.id))
        elsewhere = await login(app, SA_EMAIL, tenant_id=str(world.shree.id))
        who = await req(app, "GET", "/api/v1/whoami", token=inside.json()["access_token"])
    assert inside.status_code == 200 and inside.json()["tenant"]["id"] == str(tenant.id)
    assert who.json()["kind"] == "tenant"
    assert elsewhere.status_code == 403 and elsewhere.json()["code"] == "not_member"


async def test_only_the_super_admin_has_an_operator_view(world: World) -> None:
    asha = world.user("asha@shree.example")
    world.member(asha, world.shree)
    with auth_app(world) as app:
        token = (await login(app, asha.email)).json()["access_token"]
        res = await _post(app, "switch-admin", token)
        keyed = await req(app, "POST", "/api/v1/auth/switch-admin", key=ADMIN)
    assert res.status_code == 403 and res.json()["code"] == "not_superadmin"
    assert keyed.status_code == 403 and keyed.json()["code"] == "session_required"


async def test_a_workspace_token_dies_with_the_membership(world: World) -> None:
    """Once the account is no longer a member (bootstrap removes a demoted super admin's
    membership), its token for the workspace stops working: nothing falls back to admin."""
    sa = await _super_admin_in(world)
    tenant = _operator(world)
    with auth_app(world) as app:
        signed = (await login(app, SA_EMAIL, tenant_id=str(tenant.id))).json()
        for m in [m for m in world.db.all(TenantMember) if m.user_id == sa.id]:
            world.db.rows[TenantMember].remove(m)
        res = await req(app, "GET", "/api/v1/whoami", token=signed["access_token"])
    assert res.status_code == 403 and res.json()["code"] == "not_member"


async def test_a_super_admin_token_for_a_customer_tenant_fails_closed(world: World) -> None:
    """The middleware itself refuses a super-admin token naming a non-operator tenant
    (such a token is never issued; this is the belt to the braces)."""
    from callobuzz_rag.accounts.tokens import issue_access_token

    sa = await _super_admin_in(world)
    world.member(sa, world.shree)
    with auth_app(world) as app:
        row = (await login(app, SA_EMAIL)).json()
        session_id = _claims(app, row["access_token"]).session_id
        forged, _ = issue_access_token(
            app.state.settings, user_id=sa.id, session_id=session_id, tenant_id=world.shree.id
        )
        res = await req(app, "GET", "/api/v1/whoami", token=forged)
    assert res.status_code == 403 and res.json()["code"] == "not_member"
