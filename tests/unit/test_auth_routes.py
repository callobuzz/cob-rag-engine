"""/api/v1/auth routes end to end through the real app and middleware (accounts A3).

The database and Redis are the in-memory fakes in `accounts_fakes.py`; the integration
test `tests/integration/test_accounts_flow.py` runs the main flow on PostgreSQL + Redis.
"""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.accounts.codes import issue_code
from callobuzz_rag.accounts.ratelimit import AccountRateLimiter
from callobuzz_rag.accounts.tokens import decode_access_token
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.email_code import EmailCode
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User
from callobuzz_rag.db.models.user_session import UserSession
from tests.unit.accounts_fakes import FakeDB, FakeRedis

ADMIN = "admin-key-for-auth-tests"
SECRET = "s" * 48
SA_EMAIL = "ops@operator.example"
PASSWORD = "violet-harbour-lantern"
NEW_PASSWORD = "copper-meadow-whistle"
HASH = PasswordHasher().hash(PASSWORD)
NOW = datetime.now(UTC)


class World:
    def __init__(self) -> None:
        self.db = FakeDB()
        self.redis = FakeRedis()
        self.mail: list[tuple[str, str, dict[str, Any]]] = []
        self.shree = self.tenant("Shree Labs")
        self.pune = self.tenant("Pune Agency")

    def tenant(self, name: str, *, active: bool = True) -> Tenant:
        return self.db.add(
            Tenant(
                name=name,
                status="active" if active else "suspended",
                max_corpora=10,
                api_key_hash="",
            )
        )

    def user(
        self,
        email: str,
        *,
        password: str | None = HASH,
        verified: bool = True,
        active: bool = True,
        superadmin: bool = False,
    ) -> User:
        return self.db.add(
            User(
                email=email,
                name=email.split("@")[0].title(),
                password_hash=password,
                email_verified_at=NOW if verified else None,
                is_active=active,
                is_superadmin=superadmin,
            )
        )

    def member(self, user: User, tenant: Tenant, role: str = "owner", *, age_s: int = 0) -> None:
        self.db.add(
            TenantMember(
                tenant_id=tenant.id,
                user_id=user.id,
                role=role,
                created_at=NOW - timedelta(seconds=age_s),
            )
        )

    def sessions(self, user: User) -> list[UserSession]:
        return [s for s in self.db.all(UserSession) if s.user_id == user.id]

    def audits(self, action: str) -> list[AuditLog]:
        return [a for a in self.db.all(AuditLog) if a.action == action]


@contextmanager
def auth_app(world: World, **settings: Any) -> Iterator[Any]:
    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.api.deps import get_session

    with patch.dict(os.environ, {"RAG_ENABLE_CONTENT_PORTAL": "false"}):
        app = create_app()
    values: dict[str, Any] = {
        "admin_api_key": ADMIN,
        "session_secret": SECRET,
        "superadmin_email": SA_EMAIL,
        **settings,
    }
    app.state.settings = Settings(_env_file=None, **values)
    app.state.session_factory = world.db.factory()
    app.state.account_rate_limiter = AccountRateLimiter(client=world.redis)

    async def session_override() -> Any:
        yield world.db.session()

    app.dependency_overrides[get_session] = session_override

    def fake_enqueue(template: str, to: str, context: Any, *, expires_s: float) -> None:
        world.mail.append((template, to, dict(context)))

    with patch("callobuzz_rag.accounts.email.dispatch.enqueue_email", fake_enqueue):
        yield app


async def req(
    app: Any, method: str, path: str, *, token: str | None = None, key: str | None = None, **kw: Any
) -> Any:
    headers = dict(kw.pop("headers", {}))
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if key:
        headers["X-API-Key"] = key
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.request(method, path, headers=headers, **kw)


async def login(app: Any, email: str, password: str = PASSWORD, **extra: Any) -> Any:
    return await req(
        app, "POST", "/api/v1/auth/login", json={"email": email, "password": password, **extra}
    )


def _claims(app: Any, token: str) -> Any:
    return decode_access_token(app.state.settings, token)


@pytest.fixture
def world() -> World:
    return World()


# ------------------------------------------------------------------- login


async def test_login_success(world: World) -> None:
    asha = world.user("asha@shree.example")
    world.member(asha, world.shree, age_s=100)
    world.member(asha, world.pune, "member", age_s=10)
    with auth_app(world) as app:
        res = await login(app, "  ASHA@shree.example ")
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {
        "access_token",
        "token_type",
        "expires_in",
        "refresh_token",
        "refresh_expires_at",
        "user",
        "tenant",
        "tenants",
        "plan_choice",
    }
    assert body["plan_choice"] is None  # P6: only a sign-up verify carries the chosen plan
    assert body["token_type"] == "bearer" and body["expires_in"] == 900
    assert body["user"] == {
        "id": str(asha.id),
        "email": "asha@shree.example",
        "name": "Asha",
        "is_superadmin": False,
    }
    # the earliest membership is the default workspace
    assert body["tenant"] == {
        "id": str(world.shree.id),
        "name": "Shree Labs",
        "role": "owner",
        "status": "active",
        "is_operator": False,
    }
    assert [t["name"] for t in body["tenants"]] == ["Shree Labs", "Pune Agency"]
    assert body["refresh_token"].startswith("rt_")
    assert asha.last_login_at is not None
    (row,) = world.sessions(asha)
    assert row.tenant_id == world.shree.id and row.user_agent and row.ip
    assert _claims(app, body["access_token"]).tenant_id == world.shree.id


async def test_login_tenant_choice(world: World) -> None:
    asha = world.user("asha@shree.example")
    world.member(asha, world.shree, age_s=100)
    world.member(asha, world.pune, "member")
    other = world.tenant("Elsewhere")
    with auth_app(world) as app:
        chosen = await login(app, asha.email, tenant_id=str(world.pune.id))
        refused = await login(app, asha.email, tenant_id=str(other.id))
    assert chosen.status_code == 200 and chosen.json()["tenant"]["role"] == "member"
    assert refused.status_code == 403 and refused.json()["code"] == "not_member"


async def test_unknown_email_and_wrong_password_look_identical(world: World) -> None:
    world.user("asha@shree.example")
    world.user("nopass@shree.example", password=None)
    world.user("gone@shree.example", active=False)
    with auth_app(world) as app:
        results = [
            await login(app, "nobody@shree.example"),
            await login(app, "asha@shree.example", "wrong-password-x"),
            await login(app, "nopass@shree.example"),
            await login(app, "gone@shree.example"),
        ]
    bodies = {(r.status_code, r.text) for r in results}
    assert bodies == {(401, '{"detail":"Wrong e-mail or password","code":"invalid_credentials"}')}


async def test_distinct_refusals_only_after_the_right_password(world: World) -> None:
    world.user("new@shree.example", verified=False)
    world.user("alone@shree.example")
    with auth_app(world) as app:
        wrong = await login(app, "new@shree.example", "wrong-password-x")
        unverified = await login(app, "new@shree.example")
        alone = await login(app, "alone@shree.example")
    assert wrong.status_code == 401 and wrong.json()["code"] == "invalid_credentials"
    assert unverified.status_code == 403 and unverified.json()["code"] == "email_not_verified"
    assert alone.status_code == 403 and alone.json()["code"] == "no_workspace"


async def test_super_admin_login_has_no_tenant(world: World) -> None:
    world.user(SA_EMAIL, superadmin=True)
    with auth_app(world) as app:
        res = await login(app, SA_EMAIL)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["tenant"] is None and body["tenants"] == []
        assert body["user"]["is_superadmin"] is True
        assert _claims(app, body["access_token"]).tenant_id is None
        who = await req(app, "GET", "/api/v1/whoami", token=body["access_token"])
        admin_only = await req(app, "GET", "/api/v1/admin/settings", token=body["access_token"])
    assert who.json()["kind"] == "admin" and who.json()["auth"] == "session"
    assert who.json()["user"]["email"] == SA_EMAIL
    assert admin_only.status_code == 200, admin_only.text


async def test_super_admin_flag_without_the_env_address_is_not_admin(world: World) -> None:
    world.user("old-ops@operator.example", superadmin=True)
    with auth_app(world) as app:
        res = await login(app, "old-ops@operator.example")
    assert res.status_code == 403 and res.json()["code"] == "no_workspace"


async def test_rehash_on_login(world: World) -> None:
    weak = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(PASSWORD)
    asha = world.user("asha@shree.example", password=weak)
    world.member(asha, world.shree)
    with auth_app(world) as app:
        assert (await login(app, asha.email)).status_code == 200
    assert asha.password_hash != weak and "m=65536,t=3,p=4" in (asha.password_hash or "")


async def test_lockout(world: World) -> None:
    asha = world.user("asha@shree.example")
    world.member(asha, world.shree)
    with auth_app(world) as app:
        statuses = [
            (await login(app, asha.email, "wrong-password-x")).status_code for _ in range(5)
        ]
        locked = await login(app, asha.email)  # even the right password
        unknown = [(await login(app, "x@nowhere.example", "p" * 12)).status_code for _ in range(6)]
    assert statuses == [401] * 5
    assert locked.status_code == 429 and locked.json()["code"] == "account_locked"
    assert locked.headers["retry-after"] == "60"
    assert unknown == [401] * 5 + [429]  # the same for an address with no account
    (audit,) = [a for a in world.audits("user.login_locked") if a.target_id == str(asha.id)]
    assert audit.details == {"failures": 5}


async def test_login_rate_limit_per_ip(world: World) -> None:
    with auth_app(world) as app:
        statuses = [
            (await login(app, f"u{i}@nowhere.example", "p" * 12)).status_code for i in range(31)
        ]
    assert statuses[:30] == [401] * 30 and statuses[30] == 429


async def test_redis_down_fails_closed(world: World) -> None:
    world.redis.down = True
    world.user("asha@shree.example")
    with auth_app(world) as app:
        res = await login(app, "asha@shree.example")
    assert res.status_code == 503 and res.json()["code"] == "rate_limiter_unavailable"


async def test_accounts_disabled(world: World) -> None:
    with auth_app(world, session_secret="") as app:
        res = await login(app, "asha@shree.example")
        bearer = await req(app, "GET", "/api/v1/auth/me", token="a.b.c")
        keyed = await req(app, "GET", "/api/v1/whoami", key=ADMIN)
    assert res.status_code == 503 and res.json()["code"] == "accounts_not_configured"
    assert bearer.status_code == 503 and bearer.json()["code"] == "accounts_not_configured"
    assert keyed.status_code == 200  # API keys are unaffected


async def test_unknown_fields_are_refused(world: World) -> None:
    with auth_app(world) as app:
        res = await login(app, "asha@shree.example", is_superadmin=True)
    assert res.status_code == 422


# ----------------------------------------------------------------- refresh


async def _signed_in(world: World, app: Any, email: str = "asha@shree.example") -> dict[str, Any]:
    res = await login(app, email)
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    return body


def _asha(world: World) -> User:
    asha = world.user("asha@shree.example")
    world.member(asha, world.shree)
    return asha


async def test_refresh_rotates(world: World) -> None:
    asha = _asha(world)
    with auth_app(world) as app:
        first = await _signed_in(world, app)
        res = await req(
            app, "POST", "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
        )
    assert res.status_code == 200, res.text
    second = res.json()
    assert second["refresh_token"] != first["refresh_token"]
    (row,) = world.sessions(asha)
    assert row.last_used_at is not None and row.previous_refresh_token_hash is not None
    assert second["tenant"]["id"] == str(world.shree.id)


async def test_reuse_of_the_previous_token_revokes_the_session(world: World) -> None:
    asha = _asha(world)
    with auth_app(world) as app:
        first = await _signed_in(world, app)
        second = (
            await req(
                app, "POST", "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
            )
        ).json()
        reused = await req(
            app, "POST", "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
        )
        latest = await req(
            app, "POST", "/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}
        )
        access = await req(app, "GET", "/api/v1/auth/me", token=second["access_token"])
    assert reused.status_code == 401 and reused.json()["code"] == "refresh_token_reused"
    assert latest.status_code == 401 and latest.json()["code"] == "session_revoked"
    assert access.status_code == 401 and access.json()["code"] == "session_revoked"
    (row,) = world.sessions(asha)
    assert row.revoked_reason == "refresh_reuse"
    assert len(world.audits("session.refresh_reused")) == 1


async def test_guessed_token_does_not_revoke(world: World) -> None:
    asha = _asha(world)
    with auth_app(world) as app:
        first = await _signed_in(world, app)
        (row,) = world.sessions(asha)
        guessed = await req(
            app, "POST", "/api/v1/auth/refresh", json={"refresh_token": f"rt_{row.id.hex}_x"}
        )
        garbage = await req(app, "POST", "/api/v1/auth/refresh", json={"refresh_token": "nope"})
        ok = await req(
            app, "POST", "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
        )
    assert guessed.status_code == 401 and guessed.json()["code"] == "refresh_token_invalid"
    assert garbage.status_code == 401 and garbage.json()["code"] == "refresh_token_invalid"
    assert ok.status_code == 200
    assert row.revoked_at is None


async def test_absolute_expiry(world: World) -> None:
    asha = _asha(world)
    with auth_app(world) as app:
        first = await _signed_in(world, app)
        (row,) = world.sessions(asha)
        row.absolute_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        res = await req(
            app, "POST", "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
        )
    assert res.status_code == 401 and res.json()["code"] == "refresh_token_invalid"


async def test_refresh_never_passes_the_absolute_cap(world: World) -> None:
    asha = _asha(world)
    with auth_app(world) as app:
        first = await _signed_in(world, app)
        (row,) = world.sessions(asha)
        cap = datetime.now(UTC) + timedelta(days=2)
        row.absolute_expires_at = cap
        res = await req(
            app, "POST", "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
        )
    assert res.status_code == 200
    assert row.expires_at == cap


async def test_refresh_moves_to_the_remaining_membership(world: World) -> None:
    asha = world.user("asha@shree.example")
    world.member(asha, world.shree, age_s=100)
    world.member(asha, world.pune, "member", age_s=10)
    with auth_app(world) as app:
        first = await _signed_in(world, app)
        world.db.rows[TenantMember] = [
            m for m in world.db.all(TenantMember) if m.tenant_id != world.shree.id
        ]
        res = await req(
            app, "POST", "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
        )
        assert res.status_code == 200, res.text
        assert res.json()["tenant"]["id"] == str(world.pune.id)
        assert _claims(app, res.json()["access_token"]).tenant_id == world.pune.id
    assert world.sessions(asha)[0].tenant_id == world.pune.id


async def test_refresh_without_any_membership_or_when_inactive(world: World) -> None:
    asha = _asha(world)
    with auth_app(world) as app:
        first = await _signed_in(world, app)
        world.db.rows[TenantMember] = []
        gone = await req(
            app, "POST", "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
        )
    assert gone.status_code == 401 and gone.json()["code"] == "session_revoked"
    world.member(asha, world.shree)
    with auth_app(world) as app:
        again = await _signed_in(world, app)
        asha.is_active = False
        inactive = await req(
            app, "POST", "/api/v1/auth/refresh", json={"refresh_token": again["refresh_token"]}
        )
    assert inactive.status_code == 401 and inactive.json()["code"] == "session_revoked"


# ------------------------------------------------------------------ logout


async def test_logout_is_idempotent(world: World) -> None:
    asha = _asha(world)
    with auth_app(world) as app:
        first = await _signed_in(world, app)
        body = {"refresh_token": first["refresh_token"]}
        a = await req(app, "POST", "/api/v1/auth/logout", json=body)
        b = await req(app, "POST", "/api/v1/auth/logout", json=body)
        c = await req(app, "POST", "/api/v1/auth/logout", json={"refresh_token": "junk"})
        d = await req(
            app, "POST", "/api/v1/auth/logout", json={"refresh_token": f"rt_{uuid.uuid4().hex}_x"}
        )
        after = await req(app, "GET", "/api/v1/auth/me", token=first["access_token"])
    assert [r.status_code for r in (a, b, c, d)] == [204] * 4
    (row,) = world.sessions(asha)
    assert row.revoked_reason == "logout"
    assert after.status_code == 401 and after.json()["code"] == "session_revoked"


# ------------------------------------------------------------------- reset


async def _code(world: World, app: Any, email: str, purpose: str = "reset") -> str:
    return await issue_code(
        world.db.session(),
        settings=app.state.settings,
        email=email,
        purpose=purpose,
        user_id=None,
    )


async def test_weak_password_does_not_consume_the_code(world: World) -> None:
    _asha(world)
    with auth_app(world) as app:
        code = await _code(world, app, "asha@shree.example")
        weak = await req(
            app,
            "POST",
            "/api/v1/auth/reset",
            json={"email": "asha@shree.example", "code": code, "new_password": "password"},
        )
    assert weak.status_code == 422
    assert weak.json()["code"] == "weak_password"
    assert weak.json()["reasons"] == ["too_short", "common"]
    (row,) = world.db.all(EmailCode)
    assert row.used_at is None and row.attempts == 0


async def test_reset_sets_the_password_and_ends_every_session(world: World) -> None:
    asha = _asha(world)
    asha.email_verified_at = None
    with auth_app(world) as app:
        asha.email_verified_at = NOW
        first = await _signed_in(world, app)
        second = await _signed_in(world, app)
        asha.email_verified_at = None
        code = await _code(world, app, asha.email)
        res = await req(
            app,
            "POST",
            "/api/v1/auth/reset",
            json={"email": "ASHA@shree.example", "code": code, "new_password": NEW_PASSWORD},
        )
        assert res.status_code == 200 and res.json() == {"reset": True}
        old = await login(app, asha.email)
        new = await login(app, asha.email, NEW_PASSWORD)
        stale = await req(app, "GET", "/api/v1/auth/me", token=first["access_token"])
    assert {s.revoked_reason for s in world.sessions(asha) if s.revoked_at} == {"password_reset"}
    assert len([s for s in world.sessions(asha) if s.revoked_at]) == 2
    assert first["refresh_token"] != second["refresh_token"]
    assert asha.email_verified_at is not None and asha.password_changed_at is not None
    assert old.status_code == 401 and new.status_code == 200
    assert stale.status_code == 401
    assert [m[0] for m in world.mail] == ["password_changed"]
    assert len(world.audits("user.password_reset")) == 1


async def test_reset_wrong_code(world: World) -> None:
    _asha(world)
    with auth_app(world) as app:
        code = await _code(world, app, "asha@shree.example")
        res = await req(
            app,
            "POST",
            "/api/v1/auth/reset",
            json={
                "email": "asha@shree.example",
                "code": f"{(int(code) + 1) % 10**6:06d}",
                "new_password": NEW_PASSWORD,
            },
        )
        none = await req(
            app,
            "POST",
            "/api/v1/auth/reset",
            json={"email": "x@nowhere.example", "code": "123456", "new_password": NEW_PASSWORD},
        )
    assert res.status_code == 400 and res.json()["code"] == "invalid_code"
    assert none.status_code == 400 and none.json()["code"] == "code_expired"


async def test_reset_mail_failure_does_not_fail_the_reset(world: World) -> None:
    _asha(world)
    with auth_app(world) as app:
        code = await _code(world, app, "asha@shree.example")

        def boom(*a: Any, **k: Any) -> None:
            raise RuntimeError("broker down")

        with patch("callobuzz_rag.accounts.email.dispatch.enqueue_email", boom):
            res = await req(
                app,
                "POST",
                "/api/v1/auth/reset",
                json={"email": "asha@shree.example", "code": code, "new_password": NEW_PASSWORD},
            )
    assert res.status_code == 200


# --------------------------------------------------------------- signed in


async def test_me_and_patch_me(world: World) -> None:
    asha = _asha(world)
    with auth_app(world) as app:
        body = await _signed_in(world, app)
        me = await req(app, "GET", "/api/v1/auth/me", token=body["access_token"])
        patched = await req(
            app, "PATCH", "/api/v1/auth/me", token=body["access_token"], json={"name": "Asha K"}
        )
        empty = await req(
            app, "PATCH", "/api/v1/auth/me", token=body["access_token"], json={"name": ""}
        )
    assert me.status_code == 200
    assert me.json()["session_id"] == str(world.sessions(asha)[0].id)
    assert me.json()["tenant"]["id"] == str(world.shree.id)
    assert patched.status_code == 200 and patched.json()["user"]["name"] == "Asha K"
    assert asha.name == "Asha K"
    assert empty.status_code == 422


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("GET", "/api/v1/auth/me", None),
        ("PATCH", "/api/v1/auth/me", {"name": "x"}),
        ("POST", "/api/v1/auth/change-password", {"current_password": "a", "new_password": "b"}),
        ("GET", "/api/v1/auth/sessions", None),
        ("DELETE", f"/api/v1/auth/sessions/{uuid.uuid4()}", None),
        ("POST", "/api/v1/auth/sessions/revoke-others", None),
        ("POST", "/api/v1/auth/switch-tenant", {"tenant_id": str(uuid.uuid4())}),
    ],
)
async def test_session_routes_refuse_api_keys(
    world: World, method: str, path: str, json: Any
) -> None:
    with auth_app(world) as app:
        keyed = await req(app, method, path, key=ADMIN, json=json)
        anonymous = await req(app, method, path, json=json)
    assert keyed.status_code == 403 and keyed.json()["code"] == "session_required"
    assert anonymous.status_code == 401 and anonymous.json()["code"] == "auth_required"


async def test_change_password(world: World) -> None:
    asha = _asha(world)
    with auth_app(world) as app:
        other = await _signed_in(world, app)
        current = await _signed_in(world, app)
        token = current["access_token"]
        wrong = await req(
            app,
            "POST",
            "/api/v1/auth/change-password",
            token=token,
            json={"current_password": "not-it-at-all", "new_password": NEW_PASSWORD},
        )
        weak = await req(
            app,
            "POST",
            "/api/v1/auth/change-password",
            token=token,
            json={"current_password": PASSWORD, "new_password": "asha-shree-1"},
        )
        ok = await req(
            app,
            "POST",
            "/api/v1/auth/change-password",
            token=token,
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        )
        still = await req(app, "GET", "/api/v1/auth/me", token=token)
        ended = await req(app, "GET", "/api/v1/auth/me", token=other["access_token"])
    assert wrong.status_code == 401 and wrong.json()["code"] == "invalid_credentials"
    assert weak.status_code == 422 and weak.json()["reasons"] == ["contains_email"]
    assert ok.status_code == 204
    assert still.status_code == 200 and ended.status_code == 401
    reasons = {s.revoked_reason for s in world.sessions(asha)}
    assert reasons == {None, "password_changed"}
    assert [m[0] for m in world.mail] == ["password_changed"]
    assert len(world.audits("user.password_changed")) == 1


async def test_change_password_rate_limit(world: World) -> None:
    _asha(world)
    with auth_app(world) as app:
        token = (await _signed_in(world, app))["access_token"]
        statuses = [
            (
                await req(
                    app,
                    "POST",
                    "/api/v1/auth/change-password",
                    token=token,
                    json={"current_password": "wrong-wrong-1", "new_password": NEW_PASSWORD},
                )
            ).status_code
            for _ in range(6)
        ]
    assert statuses == [401] * 5 + [429]


async def test_sessions_list_revoke_and_revoke_others(world: World) -> None:
    asha = _asha(world)
    bob = world.user("bob@shree.example")
    world.member(bob, world.shree, "member")
    with auth_app(world) as app:
        a = await _signed_in(world, app)
        b = await _signed_in(world, app)
        c = await _signed_in(world, app)
        bobs = await _signed_in(world, app, "bob@shree.example")
        rows = sorted(world.sessions(asha), key=lambda s: s.created_at)
        for i, row in enumerate(rows):  # a, b, c in creation order
            row.created_at = NOW + timedelta(seconds=i)
        listed = await req(app, "GET", "/api/v1/auth/sessions", token=c["access_token"])
        bob_row = world.sessions(bob)[0]
        foreign = await req(
            app, "DELETE", f"/api/v1/auth/sessions/{bob_row.id}", token=c["access_token"]
        )
        unknown = await req(
            app, "DELETE", f"/api/v1/auth/sessions/{uuid.uuid4()}", token=c["access_token"]
        )
        one = await req(
            app, "DELETE", f"/api/v1/auth/sessions/{rows[0].id}", token=c["access_token"]
        )
        others = await req(
            app, "POST", "/api/v1/auth/sessions/revoke-others", token=c["access_token"]
        )
        after = await req(app, "GET", "/api/v1/auth/sessions", token=c["access_token"])
        own = await req(
            app, "DELETE", f"/api/v1/auth/sessions/{rows[2].id}", token=c["access_token"]
        )
    items = listed.json()["items"]
    assert [i["id"] for i in items] == [str(r.id) for r in reversed(rows)]  # newest first
    assert [i["current"] for i in items] == [True, False, False]
    assert set(items[0]) == {
        "id",
        "created_at",
        "last_used_at",
        "expires_at",
        "user_agent",
        "ip",
        "current",
    }
    assert foreign.status_code == 404 and unknown.status_code == 404
    assert bob_row.revoked_at is None and bobs
    assert one.status_code == 204 and rows[0].revoked_reason == "revoked"
    assert others.json() == {"revoked": 1} and rows[1].revoked_reason == "revoke_others"
    assert [i["current"] for i in after.json()["items"]] == [True]
    assert own.status_code == 204 and rows[2].revoked_reason == "logout"
    assert a and b


async def test_switch_tenant(world: World) -> None:
    asha = world.user("asha@shree.example")
    world.member(asha, world.shree, age_s=100)
    world.member(asha, world.pune, "member")
    stranger = world.tenant("Elsewhere")
    world.user(SA_EMAIL, superadmin=True)
    with auth_app(world) as app:
        body = await _signed_in(world, app)
        ok = await req(
            app,
            "POST",
            "/api/v1/auth/switch-tenant",
            token=body["access_token"],
            json={"tenant_id": str(world.pune.id)},
        )
        assert ok.status_code == 200, ok.text
        switched = ok.json()
        assert set(switched) == {"access_token", "token_type", "expires_in", "tenant"}
        assert switched["tenant"] == {
            "id": str(world.pune.id),
            "name": "Pune Agency",
            "role": "member",
            "status": "active",
            "is_operator": False,
        }
        assert _claims(app, switched["access_token"]).tenant_id == world.pune.id
        who = await req(app, "GET", "/api/v1/whoami", token=switched["access_token"])
        refused = await req(
            app,
            "POST",
            "/api/v1/auth/switch-tenant",
            token=body["access_token"],
            json={"tenant_id": str(stranger.id)},
        )
        sa = await _signed_in(world, app, SA_EMAIL)
        sa_switch = await req(
            app,
            "POST",
            "/api/v1/auth/switch-tenant",
            token=sa["access_token"],
            json={"tenant_id": str(world.shree.id)},
        )
    assert world.sessions(asha)[0].tenant_id == world.pune.id
    assert who.json()["tenant_id"] == str(world.pune.id) and who.json()["role"] == "member"
    assert refused.status_code == 403 and refused.json()["code"] == "not_member"
    assert sa_switch.status_code == 403 and sa_switch.json()["code"] == "not_member"


# L5a demo: the shared, public demo sign-ins are never locked or rate limited per address
DEMO_OWNER = "owner@demo.example"


@pytest.mark.parametrize("who", [DEMO_OWNER, SA_EMAIL])
async def test_demo_sign_ins_are_not_limited_or_locked_per_address(world: World, who: str) -> None:
    owner = world.user(DEMO_OWNER)
    world.member(owner, world.shree)
    world.user(SA_EMAIL, superadmin=True)
    extra = {"demo_mode": True, "demo_password": PASSWORD, "demo_owner_email": DEMO_OWNER}
    with auth_app(world, **extra) as app:
        wrong = [(await login(app, who, "wrong-password-x")).status_code for _ in range(6)]
        assert wrong == [401] * 6  # the per-IP limit (30) still applies, so 17 calls stay under
        ok = [(await login(app, who)).status_code for _ in range(11)]
        assert ok == [200] * 11
    assert world.audits("user.login_locked") == []


async def test_a_normal_install_still_locks_the_same_addresses(world: World) -> None:
    owner = world.user(DEMO_OWNER)
    world.member(owner, world.shree)
    with auth_app(world) as app:
        wrong = [(await login(app, DEMO_OWNER, "wrong-password-x")).status_code for _ in range(5)]
        locked = await login(app, DEMO_OWNER)
    assert wrong == [401] * 5 and locked.status_code == 429


async def test_demo_mode_still_locks_other_addresses(world: World) -> None:
    asha = world.user("asha@shree.example")
    world.member(asha, world.shree)
    extra = {"demo_mode": True, "demo_password": PASSWORD, "demo_owner_email": DEMO_OWNER}
    with auth_app(world, **extra) as app:
        for _ in range(5):
            await login(app, asha.email, "wrong-password-x")
        locked = await login(app, asha.email)
    assert locked.status_code == 429
