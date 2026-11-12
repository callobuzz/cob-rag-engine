"""Invitations: invite, re-send, revoke, view and accept (accounts A5).

Through the real app and middleware, on the in-memory database of `accounts_fakes.py`
(`tests/integration/test_team_flow.py` runs the main flow on PostgreSQL + Redis).
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from callobuzz_rag.core.exceptions import EmailQueueError
from callobuzz_rag.core.secrets import generate_api_key, hash_api_key
from callobuzz_rag.db.models.api_key import ApiKey
from callobuzz_rag.db.models.invitation import Invitation
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User
from tests.unit.test_auth_routes import ADMIN, SA_EMAIL, World, auth_app, login, req

NEW_PW = "juniper-canyon-ledger"
INVITE = "/api/v1/tenants/me/members"


@pytest.fixture
def world() -> World:
    return World()


def owner(world: World, email: str = "asha@shree.example") -> User:
    user = world.user(email)
    world.member(user, world.shree, "owner")
    return user


async def owner_token(app: Any, email: str = "asha@shree.example") -> str:
    res = await login(app, email)
    assert res.status_code == 200, res.text
    return str(res.json()["access_token"])


def invitation_mail(world: World, to: str) -> list[dict[str, Any]]:
    return [c for t, x, c in world.mail if t == "invitation" and x == to]


def token_of(world: World, to: str) -> str:
    url = invitation_mail(world, to)[-1]["accept_url"]
    assert "#token=inv_" in url and "?" not in url, url  # the token only in the fragment
    return str(url.split("#token=")[1])


def open_invitations(world: World) -> list[Invitation]:
    return [i for i in world.db.all(Invitation) if i.accepted_at is None and i.revoked_at is None]


async def invite(app: Any, token: str, email: str, role: str = "member") -> Any:
    return await req(app, "POST", INVITE, token=token, json={"email": email, "role": role})


async def view(app: Any, token: str) -> Any:
    return await req(app, "POST", "/api/v1/invitations/view", json={"token": token})


async def accept(app: Any, token: str, *, bearer: str | None = None, **body: Any) -> Any:
    return await req(
        app, "POST", "/api/v1/invitations/accept", token=bearer, json={"token": token, **body}
    )


# ------------------------------------------------------------------- invite


async def test_owner_invite_mails_the_link_and_never_returns_it(world: World) -> None:
    owner(world)
    with auth_app(world) as app:
        at = await owner_token(app)
        res = await invite(app, at, "  Ravi@Example.com ")
    assert res.status_code == 201, res.text
    body = res.json()
    assert set(body) == {"id", "email", "role", "invited_by", "created_at", "expires_at"}
    assert body["email"] == "ravi@example.com" and body["role"] == "member"
    assert body["invited_by"] == "Asha"
    assert "inv_" not in res.text
    (mail,) = invitation_mail(world, "ravi@example.com")
    assert mail["tenant_name"] == "Shree Labs" and mail["role"] == "member"
    assert mail["inviter_name"] == "Asha" and mail["days"] == 7
    assert mail["accept_url"].startswith("http://127.0.0.1:8090/invite#token=inv_")
    (row,) = world.db.all(Invitation)
    assert row.token_hash != token_of(world, "ravi@example.com")  # only the hash is stored
    assert len(row.token_hash) == 64
    expires = datetime.fromisoformat(body["expires_at"])
    assert timedelta(days=6, hours=23) < expires - datetime.now(UTC) <= timedelta(days=7)
    (audit,) = world.audits("member.invited")
    assert audit.details == {"email": "ravi@example.com", "role": "member"}
    assert audit.tenant_id == world.shree.id


async def test_invitation_mail_expires_with_the_link(world: World) -> None:
    owner(world)
    seen: list[float] = []
    with auth_app(world) as app:
        at = await owner_token(app)

        def capture(template: str, to: str, context: Any, *, expires_s: float) -> None:
            seen.append(expires_s)

        with patch("callobuzz_rag.accounts.email.dispatch.enqueue_email", capture):
            assert (await invite(app, at, "ravi@example.com")).status_code == 201
    assert seen == [604800]


async def test_admin_invite_returns_the_accept_url(world: World) -> None:
    with auth_app(world) as app:
        res = await req(
            app,
            "POST",
            f"/api/v1/admin/tenants/{world.shree.id}/invitations",
            key=ADMIN,
            json={"email": "new@example.com", "role": "owner"},
        )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["invited_by"] == "admin" and body["role"] == "owner"
    assert body["accept_url"] == invitation_mail(world, "new@example.com")[0]["accept_url"]
    assert body["accept_url"].startswith("http://127.0.0.1:8090/invite#token=inv_")


async def test_tenant_key_invites_as_an_owner(world: World) -> None:
    key = generate_api_key(prefix=f"tk_{str(world.shree.id)[:8]}")
    world.db.add(
        ApiKey(
            tenant_id=world.shree.id,
            id_short=str(world.shree.id)[:8],
            name="Default key",
            key_prefix="tk_...",
            key_hash=hash_api_key(key),
            permission="full",
            is_default=True,
        )
    )
    with auth_app(world) as app:
        res = await req(
            app, "POST", INVITE, key=key, json={"email": "r@x.example", "role": "owner"}
        )
    assert res.status_code == 201, res.text
    assert res.json()["invited_by"] == "tenant key" and "accept_url" not in res.json()


async def test_reinvite_revokes_the_old_link(world: World) -> None:
    owner(world)
    with auth_app(world) as app:
        at = await owner_token(app)
        await invite(app, at, "ravi@example.com")
        first = token_of(world, "ravi@example.com")
        again = await invite(app, at, "ravi@example.com", "owner")
        second = token_of(world, "ravi@example.com")
        old, new = await view(app, first), await view(app, second)
    assert again.status_code == 201
    assert old.status_code == 404 and old.json()["code"] == "invitation_invalid"
    assert new.status_code == 200 and new.json()["role"] == "owner"
    assert len(open_invitations(world)) == 1


async def test_superadmin_address_cannot_be_invited(world: World) -> None:
    owner(world)
    with auth_app(world) as app:
        res = await invite(app, await owner_token(app), SA_EMAIL.upper())
    assert res.status_code == 409 and res.json()["code"] == "superadmin_email"
    assert world.db.all(Invitation) == []


async def test_inviting_a_member_is_409(world: World) -> None:
    owner(world)
    ravi = world.user("ravi@example.com")
    world.member(ravi, world.shree, "member")
    with auth_app(world) as app:
        res = await invite(app, await owner_token(app), "ravi@example.com")
    assert res.status_code == 409 and res.json()["code"] == "already_member"


async def test_email_disabled_owner_503_admin_201(world: World) -> None:
    owner(world)
    with auth_app(world, email_provider="disabled") as app:
        at = await owner_token(app)
        mine = await invite(app, at, "ravi@example.com")
        admin = await req(
            app,
            "POST",
            f"/api/v1/admin/tenants/{world.shree.id}/invitations",
            key=ADMIN,
            json={"email": "ravi@example.com", "role": "member"},
        )
    assert mine.status_code == 503 and mine.json()["code"] == "email_not_configured"
    assert admin.status_code == 201 and admin.json()["accept_url"]
    assert world.mail == []


async def test_broker_down_is_503_email_unavailable(world: World) -> None:
    owner(world)
    with auth_app(world) as app:
        at = await owner_token(app)
        with patch(
            "callobuzz_rag.accounts.email.dispatch.enqueue_email",
            side_effect=EmailQueueError("down"),
        ):
            res = await invite(app, at, "ravi@example.com")
    assert res.status_code == 503 and res.json()["code"] == "email_unavailable"


async def test_invite_limit_per_tenant_fails_closed(world: World) -> None:
    owner(world)
    with auth_app(world) as app:
        at = await owner_token(app)
        codes = [(await invite(app, at, f"p{n}@example.com")).status_code for n in range(21)]
        world.redis.down = True
        down = await invite(app, at, "late@example.com")
    assert codes[:20] == [201] * 20
    assert codes[20] == 429
    assert down.status_code == 503 and down.json()["code"] == "rate_limiter_unavailable"


async def test_invite_needs_a_valid_address_and_role(world: World) -> None:
    owner(world)
    with auth_app(world) as app:
        at = await owner_token(app)
        bad_email = await invite(app, at, "not-an-address")
        bad_role = await invite(app, at, "ravi@example.com", "admin")
    assert bad_email.status_code == 422 and bad_role.status_code == 422


# ------------------------------------------------------------ resend / revoke


async def test_resend_replaces_the_token_and_extends(world: World) -> None:
    owner(world)
    with auth_app(world) as app:
        at = await owner_token(app)
        inv_id = (await invite(app, at, "ravi@example.com")).json()["id"]
        first = token_of(world, "ravi@example.com")
        (row,) = world.db.all(Invitation)
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)  # expired: still re-sendable
        res = await req(app, "POST", f"/api/v1/tenants/me/invitations/{inv_id}/resend", token=at)
        second = token_of(world, "ravi@example.com")
        old, new = await view(app, first), await view(app, second)
    assert res.status_code == 200, res.text
    assert res.json()["id"] == inv_id
    assert datetime.fromisoformat(res.json()["expires_at"]) > datetime.now(UTC) + timedelta(days=6)
    assert first != second
    assert old.status_code == 404 and new.status_code == 200
    assert [a.details.get("resent") for a in world.audits("member.invited")] == [None, True]


@pytest.mark.parametrize("provider", ["log", "disabled"])
async def test_admin_resend_returns_the_new_link(world: World, provider: str) -> None:
    """The operator re-sends any tenant's invitation (A8): same rules as an owner's resend,
    but the new link comes back in `accept_url`, so e-mail is optional."""
    admin_path = f"/api/v1/admin/tenants/{world.shree.id}/invitations"
    with auth_app(world, email_provider=provider) as app:
        made = await req(
            app, "POST", admin_path, key=ADMIN, json={"email": "new@example.com", "role": "owner"}
        )
        inv_id = made.json()["id"]
        first = made.json()["accept_url"].split("#token=")[1]
        res = await req(app, "POST", f"{admin_path}/{inv_id}/resend", key=ADMIN)
        second = res.json()["accept_url"].split("#token=")[1]
        old, new = await view(app, first), await view(app, second)
        pune = await req(
            app,
            "POST",
            f"/api/v1/admin/tenants/{world.pune.id}/invitations/{inv_id}/resend",
            key=ADMIN,
        )
    assert made.status_code == 201, made.text
    assert res.status_code == 200, res.text
    assert res.json()["id"] == inv_id and res.json()["role"] == "owner"
    assert first != second
    assert old.status_code == 404 and new.status_code == 200
    assert pune.status_code == 404  # not that tenant's invitation
    resent = [a for a in world.audits("member.invited") if a.details.get("resent")]
    assert len(resent) == 1 and resent[0].actor_kind == "admin_key"
    mailed = invitation_mail(world, "new@example.com")
    assert len(mailed) == (2 if provider == "log" else 0)


async def test_admin_resend_is_the_operators(world: World) -> None:
    owner(world)
    with auth_app(world) as app:
        at = await owner_token(app)
        inv_id = (await invite(app, at, "ravi@example.com")).json()["id"]
        path = f"/api/v1/admin/tenants/{world.shree.id}/invitations/{inv_id}/resend"
        as_owner = await req(app, "POST", path, token=at)
        no_key = await req(app, "POST", path)
    assert as_owner.status_code == 403
    assert no_key.status_code == 401


async def test_revoke(world: World) -> None:
    owner(world)
    with auth_app(world) as app:
        at = await owner_token(app)
        inv_id = (await invite(app, at, "ravi@example.com")).json()["id"]
        token = token_of(world, "ravi@example.com")
        path = f"/api/v1/tenants/me/invitations/{inv_id}"
        first = await req(app, "DELETE", path, token=at)
        second = await req(app, "DELETE", path, token=at)
        seen = await view(app, token)
        team = (await req(app, "GET", "/api/v1/tenants/me/members", token=at)).json()
    assert first.status_code == 204 and second.status_code == 404
    assert seen.status_code == 404
    assert team["invitations"] == []
    assert len(world.audits("invitation.revoked")) == 1


async def test_another_tenants_invitation_is_404(world: World) -> None:
    owner(world)
    other = world.user("p@pune.example")
    world.member(other, world.pune, "owner")
    with auth_app(world) as app:
        at = await owner_token(app)
        inv_id = (await invite(app, at, "ravi@example.com")).json()["id"]
        pune = await owner_token(app, "p@pune.example")
        revoke = await req(app, "DELETE", f"/api/v1/tenants/me/invitations/{inv_id}", token=pune)
        resend = await req(
            app, "POST", f"/api/v1/tenants/me/invitations/{inv_id}/resend", token=pune
        )
        admin_wrong = await req(
            app,
            "DELETE",
            f"/api/v1/admin/tenants/{world.pune.id}/invitations/{inv_id}",
            key=ADMIN,
        )
        admin_right = await req(
            app,
            "DELETE",
            f"/api/v1/admin/tenants/{world.shree.id}/invitations/{inv_id}",
            key=ADMIN,
        )
    assert revoke.status_code == 404 and resend.status_code == 404
    assert admin_wrong.status_code == 404 and admin_right.status_code == 204


# ------------------------------------------------------------ view / accept


async def test_view_shows_the_invitation(world: World) -> None:
    owner(world)
    with auth_app(world) as app:
        await invite(app, await owner_token(app), "ravi@example.com")
        res = await view(app, token_of(world, "ravi@example.com"))
        unknown = await view(app, "inv_nope")
        not_ours = await view(app, "rt_something")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tenant_name"] == "Shree Labs" and body["role"] == "member"
    assert body["email"] == "ravi@example.com" and body["invited_by"] == "Asha"
    assert body["account_exists"] is False
    for bad in (unknown, not_ours):
        assert bad.status_code == 404 and bad.json()["code"] == "invitation_invalid"


async def test_accept_as_a_new_user(world: World) -> None:
    owner(world)
    with auth_app(world) as app:
        await invite(app, await owner_token(app), "ravi@example.com")
        token = token_of(world, "ravi@example.com")
        res = await accept(app, token, name="Ravi", password=NEW_PW)
        again = await accept(app, token, name="Ravi", password=NEW_PW)
        me = await req(app, "GET", "/api/v1/auth/me", token=res.json()["access_token"])
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["access_token"] and body["refresh_token"].startswith("rt_")
    assert body["user"]["email"] == "ravi@example.com" and body["user"]["name"] == "Ravi"
    assert body["tenant"] == {
        "id": str(world.shree.id),
        "name": "Shree Labs",
        "role": "member",
        "status": "active",
        "is_operator": False,
    }
    ravi = next(u for u in world.db.all(User) if u.email == "ravi@example.com")
    assert ravi.email_verified_at is not None and ravi.password_hash.startswith("$argon2id$")
    member = next(m for m in world.db.all(TenantMember) if m.user_id == ravi.id)
    assert member.tenant_id == world.shree.id and member.role == "member"
    (row,) = world.db.all(Invitation)
    assert row.accepted_at is not None and row.accepted_by_user_id == ravi.id
    assert again.status_code == 404 and again.json()["code"] == "invitation_invalid"
    assert me.status_code == 200
    (audit,) = world.audits("invitation.accepted")
    assert audit.actor_user_id == ravi.id


async def test_accept_new_user_needs_name_and_a_strong_password(world: World) -> None:
    owner(world)
    with auth_app(world) as app:
        await invite(app, await owner_token(app), "ravi@example.com")
        token = token_of(world, "ravi@example.com")
        missing = await accept(app, token, name="Ravi")
        weak = await accept(app, token, name="Ravi", password="short")
        ok = await accept(app, token, name="Ravi", password=NEW_PW)
    assert missing.status_code == 422
    assert weak.status_code == 422 and weak.json()["code"] == "weak_password"
    assert ok.status_code == 200  # neither refusal spent the link


async def test_accept_over_an_unverified_signup(world: World) -> None:
    """A sign-up that never verified is not an account: the invitation's link verifies it."""
    owner(world)
    world.user("ravi@example.com", verified=False)
    with auth_app(world) as app:
        await invite(app, await owner_token(app), "ravi@example.com")
        token = token_of(world, "ravi@example.com")
        assert (await view(app, token)).json()["account_exists"] is False
        res = await accept(app, token, name="Ravi K", password=NEW_PW)
    assert res.status_code == 200, res.text
    (ravi,) = [u for u in world.db.all(User) if u.email == "ravi@example.com"]
    assert ravi.email_verified_at is not None and ravi.name == "Ravi K"


async def test_existing_account_must_sign_in(world: World) -> None:
    owner(world)
    ravi = world.user("ravi@example.com")
    world.member(ravi, world.pune, "owner")
    with auth_app(world) as app:
        await invite(app, await owner_token(app), "ravi@example.com")
        token = token_of(world, "ravi@example.com")
        seen = await view(app, token)
        anonymous = await accept(app, token, name="R", password=NEW_PW)
        rt = await owner_token(app, "ravi@example.com")  # signed in to Pune Agency
        signed_in = await accept(app, token, bearer=rt)
    assert seen.json()["account_exists"] is True
    assert anonymous.status_code == 409 and anonymous.json()["code"] == "account_exists"
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json() == {
        "accepted": True,
        "tenant": {
            "id": str(world.shree.id),
            "name": "Shree Labs",
            "role": "member",
            "status": "active",
            "is_operator": False,
        },
    }
    roles = {m.tenant_id: m.role for m in world.db.all(TenantMember) if m.user_id == ravi.id}
    assert roles == {world.pune.id: "owner", world.shree.id: "member"}


async def test_mismatched_session_is_403(world: World) -> None:
    owner(world)
    other = world.user("someone@else.example")
    world.member(other, world.pune, "owner")
    with auth_app(world) as app:
        await invite(app, await owner_token(app), "ravi@example.com")
        token = token_of(world, "ravi@example.com")
        res = await accept(app, token, bearer=await owner_token(app, "someone@else.example"))
        still = await view(app, token)
    assert res.status_code == 403 and res.json()["code"] == "invitation_email_mismatch"
    assert still.status_code == 200  # not spent


async def test_bad_bearer_on_accept_is_refused(world: World) -> None:
    owner(world)
    with auth_app(world) as app:
        await invite(app, await owner_token(app), "ravi@example.com")
        res = await accept(app, token_of(world, "ravi@example.com"), bearer="garbage")
    assert res.status_code == 401 and res.json()["code"] == "invalid_token"


async def test_accept_when_already_a_member_is_idempotent(world: World) -> None:
    owner(world)
    with auth_app(world) as app:
        at = await owner_token(app)
        await invite(app, at, "ravi@example.com", "owner")
        token = token_of(world, "ravi@example.com")
        ravi = world.user("ravi@example.com")  # joined meanwhile (e.g. an admin invite)
        world.member(ravi, world.shree, "member")
        res = await accept(app, token, bearer=await owner_token(app, "ravi@example.com"))
    assert res.status_code == 200, res.text
    assert res.json()["tenant"]["role"] == "member"  # the existing role is kept
    assert [m.role for m in world.db.all(TenantMember) if m.user_id == ravi.id] == ["member"]
    (row,) = world.db.all(Invitation)
    assert row.accepted_at is not None


@pytest.mark.parametrize("state", ["expired", "revoked", "accepted"])
async def test_dead_links_are_404(world: World, state: str) -> None:
    owner(world)
    with auth_app(world) as app:
        await invite(app, await owner_token(app), "ravi@example.com")
        token = token_of(world, "ravi@example.com")
        (row,) = world.db.all(Invitation)
        past = datetime.now(UTC) - timedelta(seconds=1)
        if state == "expired":
            row.expires_at = past
        elif state == "revoked":
            row.revoked_at = past
        else:
            row.accepted_at = past
        seen = await view(app, token)
        used = await accept(app, token, name="Ravi", password=NEW_PW)
    for res in (seen, used):
        assert res.status_code == 404 and res.json()["code"] == "invitation_invalid"
    assert not [u for u in world.db.all(User) if u.email == "ravi@example.com"]


async def test_superadmin_address_cannot_accept_as_new(world: World) -> None:
    """Invited before RAG_SUPERADMIN_EMAIL named that address: still refused."""
    owner(world)
    with auth_app(world) as app:
        await invite(app, await owner_token(app), "boss@example.com")
        token = token_of(world, "boss@example.com")
    with auth_app(world, superadmin_email="boss@example.com") as app:
        res = await accept(app, token, name="Boss", password=NEW_PW)
    assert res.status_code == 409 and res.json()["code"] == "superadmin_email"


async def test_accept_from_a_suspended_workspace_session(world: World) -> None:
    """Someone whose current workspace is suspended can still join another one."""
    owner(world)
    halted = world.tenant("Halted Co")
    ravi = world.user("ravi@example.com")
    world.member(ravi, halted, "owner")
    with auth_app(world) as app:
        await invite(app, await owner_token(app), "ravi@example.com")
        rt = await owner_token(app, "ravi@example.com")
        halted.status = "suspended"
        res = await accept(app, token_of(world, "ravi@example.com"), bearer=rt)
    assert res.status_code == 200, res.text


async def test_view_and_accept_are_rate_limited_per_ip(world: World) -> None:
    with auth_app(world) as app:
        views = [(await view(app, "inv_x")).status_code for _ in range(21)]
        world.redis.down = True
        down = await accept(app, "inv_x", name="R", password=NEW_PW)
    assert views[:20] == [404] * 20 and views[20] == 429
    assert down.status_code == 503 and down.json()["code"] == "rate_limiter_unavailable"


async def test_accounts_off_is_503(world: World) -> None:
    with auth_app(world, session_secret="") as app:
        res = await view(app, "inv_x")
    assert res.status_code == 503 and res.json()["code"] == "accounts_not_configured"
