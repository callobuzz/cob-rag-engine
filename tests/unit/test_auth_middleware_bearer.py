"""APIKeyAuthMiddleware: dashboard sessions via `Authorization: Bearer` (accounts A3).

A tiny app echoes `request.state`, behind the real (pure ASGI) middleware, over the
in-memory database. The scoping matrix with session principals (`sa`, `owner_a`) runs in
every `MATRIX` test through `scope_harness.py`, portal routes included.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from callobuzz_rag.accounts.tokens import issue_access_token
from callobuzz_rag.api import auth as auth_module
from callobuzz_rag.api.auth import APIKeyAuthMiddleware, _VerifiedKeys
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User
from callobuzz_rag.db.models.user_session import UserSession
from tests.unit.accounts_fakes import FakeDB

ADMIN = "admin-key-for-bearer-tests"
SECRET = "m" * 40
SA_EMAIL = "ops@operator.example"
NOW = datetime.now(UTC)
FIELDS = ("is_admin", "tenant", "corpus", "user", "role", "session_id", "auth_kind")


class World:
    def __init__(self) -> None:
        self.db = FakeDB()
        self.settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            admin_api_key=ADMIN,  # type: ignore[arg-type]
            session_secret=SECRET,  # type: ignore[arg-type]
            superadmin_email=SA_EMAIL,
        )
        self.tenant_a = self.db.add(Tenant(name="Tenant A", status="active", api_key_hash=""))
        self.tenant_b = self.db.add(Tenant(name="Tenant B", status="active", api_key_hash=""))
        self.sa = self.user(SA_EMAIL, superadmin=True)
        self.owner = self.user("owner@a.example")
        self.db.add(TenantMember(tenant_id=self.tenant_a.id, user_id=self.owner.id, role="owner"))

    def user(self, email: str, *, superadmin: bool = False) -> User:
        return self.db.add(
            User(email=email, name="x", is_active=True, is_superadmin=superadmin, password_hash="h")
        )

    def session(self, user: User, tenant: Tenant | None) -> UserSession:
        return self.db.add(
            UserSession(
                user_id=user.id,
                tenant_id=tenant.id if tenant else None,
                refresh_token_hash="0" * 64,
                expires_at=NOW + timedelta(days=30),
                absolute_expires_at=NOW + timedelta(days=90),
            )
        )

    def token(self, user: User, tenant: Tenant | None, row: UserSession | None = None) -> str:
        row = row or self.session(user, tenant)
        return issue_access_token(
            self.settings,
            user_id=user.id,
            session_id=row.id,
            tenant_id=tenant.id if tenant else None,
        )[0]

    def app(self) -> FastAPI:
        app = FastAPI()
        app.state.settings = self.settings
        app.state.session_factory = self.db.factory()

        @app.get("/api/v1/state")
        @app.get("/api/v1/health")
        async def state(request: Request) -> dict[str, Any]:
            s = request.state
            return {
                "is_admin": s.is_admin,
                "tenant": str(s.tenant.id) if s.tenant is not None else None,
                "corpus": str(s.corpus.id) if s.corpus is not None else None,
                "user": s.user.email if s.user is not None else None,
                "role": s.role,
                "session_id": str(s.session_id) if s.session_id else None,
                "auth_kind": s.auth_kind,
            }

        app.add_middleware(APIKeyAuthMiddleware)
        return app


async def get(app: FastAPI, path: str = "/api/v1/state", **headers: str) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.get(path, headers=headers)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world() -> World:
    return World()


async def test_super_admin_session_is_the_admin_principal(world: World) -> None:
    row = world.session(world.sa, None)
    res = await get(world.app(), **bearer(world.token(world.sa, None, row)))
    assert res.status_code == 200, res.text
    assert res.json() == {
        "is_admin": True,
        "tenant": None,
        "corpus": None,
        "user": SA_EMAIL,
        "role": None,
        "session_id": str(row.id),
        "auth_kind": "session",
    }


async def test_tenant_session_is_the_tenant_principal(world: World) -> None:
    row = world.session(world.owner, world.tenant_a)
    res = await get(world.app(), **bearer(world.token(world.owner, world.tenant_a, row)))
    assert res.status_code == 200, res.text
    assert res.json() == {
        "is_admin": False,
        "tenant": str(world.tenant_a.id),
        "corpus": None,
        "user": "owner@a.example",
        "role": "owner",
        "session_id": str(row.id),
        "auth_kind": "session",
    }


async def test_scheme_is_case_insensitive(world: World) -> None:
    token = world.token(world.owner, world.tenant_a)
    res = await get(world.app(), Authorization=f"bEaReR {token}")
    assert res.status_code == 200 and res.json()["auth_kind"] == "session"


async def test_api_key_wins_when_both_are_sent(world: World) -> None:
    res = await get(world.app(), **{"X-API-Key": ADMIN}, **bearer("garbage"))
    assert res.status_code == 200
    body = res.json()
    assert body["auth_kind"] == "api_key" and body["is_admin"] is True and body["user"] is None


async def test_key_branches_set_every_field(world: World) -> None:
    app = world.app()
    admin = (await get(app, **{"X-API-Key": ADMIN})).json()
    assert admin == {
        "is_admin": True,
        "tenant": None,
        "corpus": None,
        "user": None,
        "role": None,
        "session_id": None,
        "auth_kind": "api_key",
    }
    with patch.object(
        APIKeyAuthMiddleware, "_find_tenant_by_key", AsyncMock(return_value=world.tenant_a)
    ):
        tk = (await get(app, **{"X-API-Key": "tk_abc_def"})).json()
    assert tk["tenant"] == str(world.tenant_a.id) and tk["role"] == "owner"
    assert tk["auth_kind"] == "api_key" and tk["user"] is None
    corpus = type("C", (), {"id": uuid.uuid4()})()
    with patch.object(APIKeyAuthMiddleware, "_find_corpus_by_key", AsyncMock(return_value=corpus)):
        ck = (await get(app, **{"X-API-Key": "ck_abc_def"})).json()
    assert ck["corpus"] == str(corpus.id) and ck["role"] is None and ck["tenant"] is None
    public = (await get(app, "/api/v1/health", **bearer("anything"))).json()
    assert public == {**dict.fromkeys(FIELDS), "is_admin": False, "auth_kind": "none"}


async def test_basic_auth_is_ignored(world: World) -> None:
    res = await get(world.app(), Authorization="Basic dXNlcjpwYXNz")
    assert res.status_code == 401
    assert res.json() == {"detail": "API key required", "code": "auth_required"}


async def test_401_bodies_carry_codes(world: World) -> None:
    app = world.app()
    missing = await get(app)
    with patch.object(APIKeyAuthMiddleware, "_find_corpus_by_key", AsyncMock(return_value=None)):
        wrong = await get(app, **{"X-API-Key": "nope"})
    assert missing.json() == {"detail": "API key required", "code": "auth_required"}
    assert wrong.json() == {"detail": "Invalid API key", "code": "invalid_api_key"}


async def test_expired_token(world: World) -> None:
    row = world.session(world.owner, world.tenant_a)
    past = datetime.now(UTC) - timedelta(minutes=20)
    token = jwt.encode(
        {
            "iss": "cob-rag-engine",
            "aud": "cob-rag-dashboard",
            "sub": str(world.owner.id),
            "sid": str(row.id),
            "tid": str(world.tenant_a.id),
            "iat": past,
            "exp": past + timedelta(minutes=15),
            "typ": "access",
        },
        SECRET,
        algorithm="HS256",
    )
    res = await get(world.app(), **bearer(token))
    assert res.status_code == 401 and res.json()["code"] == "token_expired"


@pytest.mark.parametrize("token", ["garbage", "a.b.c", "Bearer"])
async def test_invalid_token(world: World, token: str) -> None:
    res = await get(world.app(), **bearer(token))
    assert res.status_code == 401 and res.json()["code"] == "invalid_token"


async def test_revoked_or_expired_session_or_inactive_user(world: World) -> None:
    app = world.app()
    revoked = world.session(world.owner, world.tenant_a)
    revoked.revoked_at = NOW
    expired = world.session(world.owner, world.tenant_a)
    expired.expires_at = NOW - timedelta(seconds=1)
    unknown = UserSession(id=uuid.uuid4())
    for row in (revoked, expired, unknown):
        res = await get(app, **bearer(world.token(world.owner, world.tenant_a, row)))
        assert res.status_code == 401 and res.json()["code"] == "session_revoked"
    live = world.token(world.owner, world.tenant_a)
    world.owner.is_active = False
    res = await get(app, **bearer(live))
    assert res.status_code == 401 and res.json()["code"] == "session_revoked"


async def test_membership_removed(world: World) -> None:
    token = world.token(world.owner, world.tenant_a)
    world.db.rows[TenantMember] = []
    res = await get(world.app(), **bearer(token))
    assert res.status_code == 403 and res.json()["code"] == "not_member"


async def test_token_for_a_tenant_the_user_is_not_in(world: World) -> None:
    token = world.token(world.owner, world.tenant_b)
    res = await get(world.app(), **bearer(token))
    assert res.status_code == 403 and res.json()["code"] == "not_member"


@pytest.mark.parametrize("status", ["pending", "suspended"])
async def test_inactive_workspace(world: World, status: str) -> None:
    token = world.token(world.owner, world.tenant_a)
    world.tenant_a.status = status
    res = await get(world.app(), **bearer(token))
    assert res.status_code == 403
    assert res.json() == {
        "detail": "This workspace is not active",
        "code": "workspace_not_active",
        "status": status,
    }


async def test_changing_the_super_admin_email_demotes_at_once(world: World) -> None:
    token = world.token(world.sa, None)
    world.settings = world.settings.model_copy(update={"superadmin_email": "new@operator.example"})
    res = await get(world.app(), **bearer(token))
    assert res.status_code == 401 and res.json()["code"] == "invalid_token"


async def test_a_member_without_tid_is_invalid(world: World) -> None:
    token = world.token(world.owner, None)
    res = await get(world.app(), **bearer(token))
    assert res.status_code == 401 and res.json()["code"] == "invalid_token"


async def test_accounts_disabled_refuses_bearer_only(world: World) -> None:
    token = world.token(world.owner, world.tenant_a)
    world.settings = world.settings.model_copy(update={"session_secret": SecretStr("")})
    app = world.app()
    res = await get(app, **bearer(token))
    keyed = await get(app, **{"X-API-Key": ADMIN})
    assert res.status_code == 503 and res.json()["code"] == "accounts_not_configured"
    assert keyed.status_code == 200


async def test_bearer_never_touches_the_key_cache(world: World) -> None:
    token = world.token(world.owner, world.tenant_a)
    with (
        patch.object(_VerifiedKeys, "get", side_effect=AssertionError("cache consulted")),
        patch.object(auth_module, "verify_api_key", side_effect=AssertionError("bcrypt")),
    ):
        res = await get(world.app(), **bearer(token))
    assert res.status_code == 200 and res.json()["auth_kind"] == "session"


async def test_optional_auth_paths(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module, "PUBLIC_PATHS", {*auth_module.PUBLIC_PATHS, "/api/v1/state"})
    monkeypatch.setattr(auth_module, "OPTIONAL_AUTH_PATHS", {"/api/v1/state"})
    app = world.app()
    anonymous = await get(app)
    signed_in = await get(app, **bearer(world.token(world.owner, world.tenant_a)))
    bad = await get(app, **bearer("garbage"))
    assert anonymous.status_code == 200 and anonymous.json()["auth_kind"] == "none"
    assert signed_in.status_code == 200 and signed_in.json()["user"] == "owner@a.example"
    assert bad.status_code == 401 and bad.json()["code"] == "invalid_token"


def test_public_auth_paths() -> None:
    for path in ("login", "refresh", "logout", "reset"):
        assert f"/api/v1/auth/{path}" in auth_module.PUBLIC_PATHS
    for path in ("me", "sessions", "switch-tenant", "change-password"):
        assert f"/api/v1/auth/{path}" not in auth_module.PUBLIC_PATHS
    # Accounts A5: accepting an invitation is public but resolves a sign-in when sent.
    assert {"/api/v1/invitations/accept"} == auth_module.OPTIONAL_AUTH_PATHS
    assert auth_module.OPTIONAL_AUTH_PATHS <= auth_module.PUBLIC_PATHS
    assert "/api/v1/invitations/view" in auth_module.PUBLIC_PATHS


def test_middleware_stays_pure_asgi() -> None:
    from starlette.middleware.base import BaseHTTPMiddleware

    assert not issubclass(APIKeyAuthMiddleware, BaseHTTPMiddleware)
