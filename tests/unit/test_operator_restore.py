"""POST /api/v1/admin/operator-workspace/restore: "Recreate my workspace" (decided 2026-10-02).

A workspace the operator deleted on purpose stays deleted (`operator_workspace_removed`); this
is the dashboard's way back, the same service as `bootstrap --restore-operator-workspace`.
Through the real app on the in-memory database of `accounts_fakes.py`.
"""

from typing import Any

import pytest

from callobuzz_rag.accounts import operator
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User
from tests.unit.test_auth_routes import ADMIN, SA_EMAIL, World, auth_app, login, req
from tests.unit.test_tenant_lifecycle import admin_app

PATH = "/api/v1/admin/operator-workspace/restore"


@pytest.fixture
def world() -> World:
    return World()


async def _deleted_workspace(world: World) -> None:
    """The operator had a workspace and deleted it on purpose (so the flag is set)."""
    sa = world.user(SA_EMAIL, superadmin=True)
    async with world.db.factory()() as session:
        await operator.ensure_operator_workspace(session, owner=sa)
        (tenant,) = [t for t in world.db.all(Tenant) if t.is_operator]
        await operator.set_workspace_removed(session, True)
        await session.delete(tenant)


def _operators(world: World) -> list[Tenant]:
    return [t for t in world.db.all(Tenant) if t.is_operator]


def _flag(world: World) -> bool:
    row = next((r for r in world.db.all(EngineSettingsRow)), None)
    return bool(row is not None and row.operator_workspace_removed)


async def test_it_recreates_a_deleted_workspace_and_clears_the_flag(world: World) -> None:
    await _deleted_workspace(world)
    assert _operators(world) == [] and _flag(world) is True
    with admin_app(world) as (app, _):
        res = await req(app, "POST", PATH, key=ADMIN)
    assert res.status_code == 201, res.text
    assert res.json()["is_operator"] is True
    (tenant,) = _operators(world)
    assert tenant.status == "active" and str(tenant.id) == res.json()["id"]
    assert _flag(world) is False
    owners = {m.user_id for m in world.db.all(TenantMember) if m.tenant_id == tenant.id}
    assert {u.email for u in world.db.all(User) if u.id in owners} == {SA_EMAIL}


async def test_it_is_audited_with_the_caller(world: World) -> None:
    await _deleted_workspace(world)
    with admin_app(world) as (app, _):
        await req(app, "POST", PATH, key=ADMIN)
    rows: list[Any] = [
        a for a in world.db.all(AuditLog) if a.action == "operator_workspace.created"
    ]
    restored = rows[-1]
    assert restored.actor_kind == "admin_key"
    assert restored.details["restored"] is True


async def test_it_refuses_when_the_workspace_exists_and_changes_nothing(world: World) -> None:
    await _deleted_workspace(world)
    with admin_app(world) as (app, _):
        first = await req(app, "POST", PATH, key=ADMIN)
        before = len(world.db.all(AuditLog))
        second = await req(app, "POST", PATH, key=ADMIN)
    assert first.status_code == 201
    assert second.status_code == 409 and second.json()["code"] == "operator_workspace_exists"
    assert len(_operators(world)) == 1
    assert len(world.db.all(AuditLog)) == before


async def test_it_creates_the_workspace_when_none_was_ever_made(world: World) -> None:
    """Not deleted, just missing (the flag is clear): the same call makes it."""
    world.user(SA_EMAIL, superadmin=True)
    with admin_app(world) as (app, _):
        res = await req(app, "POST", PATH, key=ADMIN)
    assert res.status_code == 201, res.text
    assert len(_operators(world)) == 1


async def test_the_super_admin_session_may_restore_it(world: World) -> None:
    await _deleted_workspace(world)
    sa = next(u for u in world.db.all(User) if u.email == SA_EMAIL)
    with admin_app(world) as (app, _):
        token = (await login(app, SA_EMAIL)).json()["access_token"]
        res = await req(app, "POST", PATH, token=token)
    assert res.status_code == 201, res.text
    (tenant,) = _operators(world)
    owners = [m for m in world.db.all(TenantMember) if m.tenant_id == tenant.id]
    assert [m.user_id for m in owners] == [sa.id]


async def test_only_the_admin_may_restore(world: World) -> None:
    await _deleted_workspace(world)
    asha = world.user("asha@shree.example")
    world.member(asha, world.shree)
    with auth_app(world) as app:
        token = (await login(app, asha.email)).json()["access_token"]
        member = await req(app, "POST", PATH, token=token)
        anonymous = await req(app, "POST", PATH)
    assert member.status_code == 403
    assert anonymous.status_code == 401
    assert _operators(world) == []
