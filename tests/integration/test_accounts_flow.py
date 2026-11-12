"""Accounts A3 on real PostgreSQL + Redis: bootstrap, set the password, sign in, refresh,
reuse detection, a workspace session through the middleware's join, the lockout, and the
phase11_003 migration down and up again.

A sync test (alembic's env.py calls asyncio.run()); the async flow runs in its own loop.
Requires Docker.
"""

import asyncio
import os
import uuid
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from tests.integration.conftest import RAG_ROOT, VALKEY_IMAGE, _async_url

pytestmark = pytest.mark.integration

SA_EMAIL = "ops@example.com"
PASSWORD = "correct horse battery"
SECRET = "f" * 64


def _alembic(url: str, target: str, *, down: bool = False) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(RAG_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAG_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    (command.downgrade if down else command.upgrade)(cfg, target)


def _redis_url(redis: RedisContainer) -> str:
    host = redis.get_container_host_ip().replace("localhost", "127.0.0.1")
    return f"redis://{host}:{redis.get_exposed_port(6379)}/0"


async def _flow(db_url: str, redis_url: str) -> dict[str, Any]:
    from callobuzz_rag.accounts.bootstrap import run
    from callobuzz_rag.accounts.ratelimit import AccountRateLimiter
    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.db.models.tenant import Tenant
    from callobuzz_rag.db.models.tenant_member import TenantMember
    from callobuzz_rag.db.models.user import User

    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        session_secret=SECRET,  # type: ignore[arg-type]
        superadmin_email=SA_EMAIL,
        email_provider="log",
        admin_api_key="admin-key-integration",  # type: ignore[arg-type]
    )
    out: dict[str, Any] = {}
    try:
        lines: list[str] = []
        out["bootstrap"] = await run(settings, factory, out=lines.append)
        second: list[str] = []
        # Before the password is set, every run issues a new code and voids the last one.
        out["bootstrap_again"] = await run(settings, factory, out=second.append)
        codes = [
            ln.split(":", 1)[1].strip() for ln in lines + second if ln.strip().startswith("code:")
        ]
        out["printed_codes"] = len(codes)
        code = codes[-1]

        with patch.dict(os.environ, {"RAG_ENABLE_CONTENT_PORTAL": "false"}):
            app = create_app()
        app.state.settings = settings
        app.state.session_factory = factory
        limiter = AccountRateLimiter(redis_url)
        app.state.account_rate_limiter = limiter
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            reset = await c.post(
                "/api/v1/auth/reset",
                json={"email": SA_EMAIL, "code": code, "new_password": PASSWORD},
            )
            out["reset"] = reset.status_code
            out["ready"] = await run(settings, factory, out=second.append)
            out["ready_line"] = second[-1]

            login = await c.post(
                "/api/v1/auth/login", json={"email": SA_EMAIL, "password": PASSWORD}
            )
            out["login"] = login.status_code
            assert login.status_code == 200, (reset.text, login.text)
            body = login.json()
            who = await c.get(
                "/api/v1/whoami", headers={"Authorization": f"Bearer {body['access_token']}"}
            )
            out["whoami"] = who.json()

            tokens = [body["refresh_token"]]
            for _ in range(3):
                r = await c.post("/api/v1/auth/refresh", json={"refresh_token": tokens[-1]})
                assert r.status_code == 200, r.text
                tokens.append(r.json()["refresh_token"])
            reused = await c.post("/api/v1/auth/refresh", json={"refresh_token": tokens[-2]})
            out["reused"] = reused.json()
            latest = await c.post("/api/v1/auth/refresh", json={"refresh_token": tokens[-1]})
            out["latest"] = latest.json()

            # A workspace member, through the middleware's session/user/member/tenant join.
            from callobuzz_rag.accounts.passwords import hash_password

            async with factory() as session:
                tenant = Tenant(name="Shree Labs", api_key_hash="")
                session.add(tenant)
                await session.flush()
                user = User(
                    email="asha@shree.example",
                    name="Asha",
                    password_hash=await hash_password(PASSWORD),
                    email_verified_at=text("now()"),
                )
                session.add(user)
                await session.flush()
                session.add(TenantMember(tenant_id=tenant.id, user_id=user.id, role="member"))
                await session.commit()
                out["tenant_id"] = str(tenant.id)
            member = await c.post(
                "/api/v1/auth/login", json={"email": "asha@shree.example", "password": PASSWORD}
            )
            out["member_login"] = member.status_code
            mwho = await c.get(
                "/api/v1/whoami",
                headers={"Authorization": f"Bearer {member.json()['access_token']}"},
            )
            out["member_whoami"] = mwho.json()
            sessions = await c.get(
                "/api/v1/auth/sessions",
                headers={"Authorization": f"Bearer {member.json()['access_token']}"},
            )
            out["member_sessions"] = sessions.json()

            wrong = []
            victim = f"nobody-{uuid.uuid4().hex[:6]}@example.com"
            for _ in range(7):
                r = await c.post(
                    "/api/v1/auth/login", json={"email": victim, "password": "wrong-password-x"}
                )
                wrong.append(r.status_code)
            out["lockout"] = wrong

        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT s.revoked_reason FROM sessions s JOIN users u ON u.id = s.user_id "
                    "WHERE u.email = :e"
                ),
                {"e": SA_EMAIL},
            )
            out["sa_sessions"] = [r[0] for r in rows]
            audits = await conn.execute(text("SELECT action FROM audit_log ORDER BY created_at"))
            out["audits"] = [r[0] for r in audits]
        await limiter.close()
    finally:
        await engine.dispose()
    return out


def test_accounts_flow_and_migration() -> None:
    with PostgresContainer("postgres:16-alpine") as pg, RedisContainer(VALKEY_IMAGE) as redis:
        url = _async_url(pg)
        _alembic(url, "head")
        out = asyncio.run(_flow(url, _redis_url(redis)))

        assert out["bootstrap"] == 0 and out["bootstrap_again"] == 0
        assert out["printed_codes"] == 2
        assert out["reset"] == 200
        assert out["ready"] == 0 and out["ready_line"] == f"Super admin ready: {SA_EMAIL}"
        assert out["login"] == 200
        assert out["whoami"]["kind"] == "admin" and out["whoami"]["auth"] == "session"
        assert out["reused"]["code"] == "refresh_token_reused"
        assert out["latest"]["code"] == "session_revoked"
        assert out["sa_sessions"] == ["refresh_reuse"]
        assert out["member_login"] == 200
        assert out["member_whoami"]["kind"] == "tenant"
        assert out["member_whoami"]["tenant_id"] == out["tenant_id"]
        assert out["member_whoami"]["role"] == "member"
        assert [i["current"] for i in out["member_sessions"]["items"]] == [True]
        assert out["lockout"] == [401] * 5 + [429, 429]
        assert out["audits"].count("operator_workspace.created") == 1  # not once per start
        boots = [a for a in out["audits"] if a != "operator_workspace.created"]
        assert boots[:2] == ["superadmin.bootstrapped", "superadmin.bootstrapped"]
        assert "user.password_reset" in out["audits"]
        assert "session.refresh_reused" in out["audits"]
        assert "user.login_locked" in out["audits"]

        async def tables() -> set[str]:
            engine = create_async_engine(url)
            try:
                async with engine.connect() as conn:
                    rows = await conn.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                    )
                    return {r[0] for r in rows}
            finally:
                await engine.dispose()

        mine = {"users", "tenant_members", "sessions", "email_codes"}
        assert mine <= asyncio.run(tables())
        _alembic(url, "phase11_002", down=True)
        assert not (mine & asyncio.run(tables()))
        _alembic(url, "phase11_003")
        assert mine <= asyncio.run(tables())
