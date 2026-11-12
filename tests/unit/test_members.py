"""The team: list, role changes, removal, leaving and the last-owner rule (accounts A5).

Through the real app and middleware, on the in-memory database of `accounts_fakes.py`.
The lock that stops two owners demoting each other at the same moment is exercised on
PostgreSQL in `tests/integration/test_team_flow.py`.
"""

from typing import Any

import pytest

from callobuzz_rag.db.models.invitation import Invitation
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User
from tests.unit.test_auth_routes import ADMIN, World, auth_app, login, req

ME = "/api/v1/tenants/me/members"


@pytest.fixture
def world() -> World:
    return World()


def person(world: World, email: str, role: str = "member", *, age_s: int = 0) -> User:
    user = world.user(email)
    world.member(user, world.shree, role, age_s=age_s)
    return user


async def token(app: Any, email: str, tenant_id: Any = None) -> str:
    extra = {"tenant_id": str(tenant_id)} if tenant_id else {}
    res = await login(app, email, **extra)
    assert res.status_code == 200, res.text
    return str(res.json()["access_token"])


def role_of(world: World, user: User) -> str | None:
    return next(
        (
            m.role
            for m in world.db.all(TenantMember)
            if m.user_id == user.id and m.tenant_id == world.shree.id
        ),
        None,
    )


def admin_path(world: World, rest: str) -> str:
    return f"/api/v1/admin/tenants/{world.shree.id}{rest}"


# -------------------------------------------------------------------- list


async def test_team_list_shapes(world: World) -> None:
    asha = person(world, "asha@shree.example", "owner", age_s=100)
    person(world, "ravi@shree.example", age_s=10)
    world.member(world.user("p@pune.example"), world.pune, "owner")
    with auth_app(world) as app:
        at = await token(app, "asha@shree.example")
        await req(app, "POST", ME, token=at, json={"email": "new@x.example", "role": "owner"})
        mt = await token(app, "ravi@shree.example")
        as_member = await req(app, "GET", ME, token=mt)
        as_admin = await req(app, "GET", admin_path(world, "/members"), key=ADMIN)
        missing = await req(
            app, "GET", f"/api/v1/admin/tenants/{world.user('z@z.example').id}/members", key=ADMIN
        )
        as_admin_me = await req(app, "GET", ME, key=ADMIN)
    assert as_member.status_code == 200, as_member.text  # members may view the team
    body = as_member.json()
    assert [m["email"] for m in body["members"]] == ["asha@shree.example", "ravi@shree.example"]
    first = body["members"][0]
    assert set(first) == {"user_id", "email", "name", "role", "joined_at", "last_login_at"}
    assert first["user_id"] == str(asha.id) and first["role"] == "owner"
    assert first["last_login_at"] is not None
    (inv,) = body["invitations"]
    assert inv["email"] == "new@x.example" and inv["role"] == "owner"
    assert set(inv) == {"id", "email", "role", "invited_by", "created_at", "expires_at"}
    assert as_admin.json() == body  # the operator sees the same team
    assert missing.status_code == 404
    assert as_admin_me.status_code == 403  # /tenants/me is a tenant's own route


async def test_expired_invitations_are_not_listed(world: World) -> None:
    from datetime import UTC, datetime, timedelta

    person(world, "asha@shree.example", "owner")
    with auth_app(world) as app:
        at = await token(app, "asha@shree.example")
        await req(app, "POST", ME, token=at, json={"email": "new@x.example", "role": "member"})
        (row,) = world.db.all(Invitation)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        body = (await req(app, "GET", ME, token=at)).json()
    assert body["invitations"] == []


# ------------------------------------------------------------------- roles


async def test_promote_and_demote(world: World) -> None:
    person(world, "asha@shree.example", "owner")
    ravi = person(world, "ravi@shree.example")
    with auth_app(world) as app:
        at = await token(app, "asha@shree.example")
        up = await req(app, "PATCH", f"{ME}/{ravi.id}", token=at, json={"role": "owner"})
        same = await req(app, "PATCH", f"{ME}/{ravi.id}", token=at, json={"role": "owner"})
        down = await req(
            app,
            "PATCH",
            admin_path(world, f"/members/{ravi.id}"),
            key=ADMIN,
            json={"role": "member"},
        )
        nobody = await req(
            app, "PATCH", f"{ME}/{world.user('z@z.example').id}", token=at, json={"role": "owner"}
        )
    assert up.status_code == 200, up.text
    assert up.json()["role"] == "owner" and up.json()["email"] == "ravi@shree.example"
    assert same.status_code == 200
    assert down.status_code == 200 and down.json()["role"] == "member"
    assert role_of(world, ravi) == "member"
    assert nobody.status_code == 404
    changes = [(a.details["from"], a.details["to"]) for a in world.audits("member.role_changed")]
    assert changes == [("member", "owner"), ("owner", "member")]  # the no-op is not audited


async def test_role_change_takes_effect_on_the_next_request(world: World) -> None:
    person(world, "asha@shree.example", "owner")
    ravi = person(world, "ravi@shree.example")
    with auth_app(world) as app:
        at = await token(app, "asha@shree.example")
        mt = await token(app, "ravi@shree.example")
        before = await req(app, "POST", "/api/v1/tenants/me/rotate-key", token=mt)
        await req(app, "PATCH", f"{ME}/{ravi.id}", token=at, json={"role": "owner"})
        after = await req(app, "POST", "/api/v1/tenants/me/rotate-key", token=mt)  # same token
    assert before.status_code == 403 and before.json()["code"] == "owner_required"
    assert after.status_code == 200, after.text


async def test_last_owner_cannot_be_demoted_removed_or_leave(world: World) -> None:
    asha = person(world, "asha@shree.example", "owner")
    person(world, "ravi@shree.example")
    with auth_app(world) as app:
        at = await token(app, "asha@shree.example")
        demote = await req(app, "PATCH", f"{ME}/{asha.id}", token=at, json={"role": "member"})
        leave = await req(app, "DELETE", f"{ME}/{asha.id}", token=at)
        admin_demote = await req(
            app,
            "PATCH",
            admin_path(world, f"/members/{asha.id}"),
            key=ADMIN,
            json={"role": "member"},
        )
        admin_remove = await req(app, "DELETE", admin_path(world, f"/members/{asha.id}"), key=ADMIN)
    for res in (demote, leave, admin_demote, admin_remove):
        assert res.status_code == 409, res.text
        assert res.json()["code"] == "last_owner"
    assert role_of(world, asha) == "owner"


async def test_two_owners_cannot_demote_each_other_to_none(world: World) -> None:
    """Owner A demotes owner B; B's next request is a member's, so B cannot demote A;
    and nobody (not even the admin) can then demote A, the last owner."""
    asha = person(world, "asha@shree.example", "owner")
    bina = person(world, "bina@shree.example", "owner")
    with auth_app(world) as app:
        at = await token(app, "asha@shree.example")
        bt = await token(app, "bina@shree.example")
        first = await req(app, "PATCH", f"{ME}/{bina.id}", token=at, json={"role": "member"})
        second = await req(app, "PATCH", f"{ME}/{asha.id}", token=bt, json={"role": "member"})
        admin = await req(
            app,
            "PATCH",
            admin_path(world, f"/members/{asha.id}"),
            key=ADMIN,
            json={"role": "member"},
        )
    assert first.status_code == 200
    assert second.status_code == 403 and second.json()["code"] == "owner_required"
    assert admin.status_code == 409 and admin.json()["code"] == "last_owner"
    assert role_of(world, asha) == "owner"


async def test_an_owner_may_step_down_when_another_owner_exists(world: World) -> None:
    asha = person(world, "asha@shree.example", "owner")
    person(world, "bina@shree.example", "owner")
    with auth_app(world) as app:
        at = await token(app, "asha@shree.example")
        res = await req(app, "PATCH", f"{ME}/{asha.id}", token=at, json={"role": "member"})
    assert res.status_code == 200 and role_of(world, asha) == "member"


# ----------------------------------------------------------------- removal


async def test_owner_removes_a_member_and_their_sessions_here_end(world: World) -> None:
    person(world, "asha@shree.example", "owner")
    ravi = person(world, "ravi@shree.example", age_s=10)
    world.member(ravi, world.pune, "owner", age_s=5)
    with auth_app(world) as app:
        at = await token(app, "asha@shree.example")
        here = await token(app, "ravi@shree.example", world.shree.id)
        there = await token(app, "ravi@shree.example", world.pune.id)
        res = await req(app, "DELETE", f"{ME}/{ravi.id}", token=at)
        again = await req(app, "DELETE", f"{ME}/{ravi.id}", token=at)
        cut = await req(app, "GET", "/api/v1/whoami", token=here)
        kept = await req(app, "GET", "/api/v1/whoami", token=there)
    assert res.status_code == 204, res.text
    assert again.status_code == 404
    assert role_of(world, ravi) is None
    assert cut.status_code == 401 and cut.json()["code"] == "session_revoked"
    assert kept.status_code == 200
    reasons = {s.tenant_id: s.revoked_reason for s in world.sessions(ravi)}
    assert reasons == {world.shree.id: "member_removed", world.pune.id: None}
    assert any(u.id == ravi.id for u in world.db.all(User))  # the account stays
    (audit,) = world.audits("member.removed")
    assert audit.details["role"] == "member" and audit.details["left"] is False
    assert audit.details["sessions_revoked"] == 1


async def test_member_leaves(world: World) -> None:
    person(world, "asha@shree.example", "owner")
    ravi = person(world, "ravi@shree.example")
    with auth_app(world) as app:
        mt = await token(app, "ravi@shree.example")
        res = await req(app, "DELETE", f"{ME}/{ravi.id}", token=mt)
    assert res.status_code == 204, res.text
    assert role_of(world, ravi) is None
    (audit,) = world.audits("member.removed")
    assert audit.details["left"] is True and audit.actor_user_id == ravi.id


async def test_member_cannot_remove_someone_else(world: World) -> None:
    asha = person(world, "asha@shree.example", "owner")
    person(world, "ravi@shree.example")
    with auth_app(world) as app:
        mt = await token(app, "ravi@shree.example")
        res = await req(app, "DELETE", f"{ME}/{asha.id}", token=mt)
    assert res.status_code == 403 and res.json()["code"] == "owner_required"
    assert role_of(world, asha) == "owner"


async def test_owner_leaves_when_another_owner_remains(world: World) -> None:
    asha = person(world, "asha@shree.example", "owner")
    person(world, "bina@shree.example", "owner")
    with auth_app(world) as app:
        at = await token(app, "asha@shree.example")
        res = await req(app, "DELETE", f"{ME}/{asha.id}", token=at)
    assert res.status_code == 204 and role_of(world, asha) is None


async def test_admin_removes_from_any_tenant(world: World) -> None:
    person(world, "asha@shree.example", "owner")
    ravi = person(world, "ravi@shree.example")
    with auth_app(world) as app:
        res = await req(app, "DELETE", admin_path(world, f"/members/{ravi.id}"), key=ADMIN)
        gone_tenant = await req(
            app, "DELETE", f"/api/v1/admin/tenants/{ravi.id}/members/{ravi.id}", key=ADMIN
        )
    assert res.status_code == 204 and role_of(world, ravi) is None
    assert gone_tenant.status_code == 404
    (audit,) = world.audits("member.removed")
    assert audit.actor_kind == "admin_key"


async def test_other_tenants_members_are_out_of_reach(world: World) -> None:
    person(world, "asha@shree.example", "owner")
    pat = world.user("p@pune.example")
    world.member(pat, world.pune, "member")
    with auth_app(world) as app:
        at = await token(app, "asha@shree.example")
        patch = await req(app, "PATCH", f"{ME}/{pat.id}", token=at, json={"role": "owner"})
        remove = await req(app, "DELETE", f"{ME}/{pat.id}", token=at)
    assert patch.status_code == 404 and remove.status_code == 404
    assert [m.role for m in world.db.all(TenantMember) if m.user_id == pat.id] == ["member"]
