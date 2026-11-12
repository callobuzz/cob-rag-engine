"""Accounts A4 on real PostgreSQL + Redis: sign-up (approval) -> verify -> pending session
blocked -> activate -> suspend (typed name) -> keys and sessions cut -> hard delete; a
declined sign-up; the admin list's filters, paging and aggregates; the cleanup task; and
the phase11_004 migration up and down with an inactive tenant.

E-mail is captured where it is queued (`enqueue_email`); Qdrant and the file-cleanup queue
are stand-ins (what they are asked to delete is asserted). A sync test (alembic's env.py
calls asyncio.run()); the async flow runs in its own loop. Requires Docker.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from tests.integration.conftest import VALKEY_IMAGE, _async_url
from tests.integration.test_accounts_flow import _alembic, _redis_url

pytestmark = pytest.mark.integration

ADMIN = "admin-key-integration"
SA_EMAIL = "ops@example.com"
PW = "a long pass phrase"


async def _flow(db_url: str, redis_url: str) -> dict[str, Any]:
    from callobuzz_rag.accounts import cleanup
    from callobuzz_rag.accounts.ratelimit import AccountRateLimiter
    from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.email_code import EmailCode
    from callobuzz_rag.db.models.user import User

    invalidate_engine_settings_cache()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        session_secret="f" * 64,  # type: ignore[arg-type]
        superadmin_email=SA_EMAIL,
        email_provider="log",
        admin_api_key=ADMIN,  # type: ignore[arg-type]
    )
    mail: list[tuple[str, str, dict[str, Any]]] = []

    def capture(template: str, to: str, context: Any, *, expires_s: float) -> None:
        mail.append((template, to, dict(context)))

    def code(to: str, template: str = "verify_code") -> str:
        return str([c for t, x, c in mail if t == template and x == to][-1]["code"])

    out: dict[str, Any] = {}
    with patch.dict(os.environ, {"RAG_ENABLE_CONTENT_PORTAL": "false"}):
        app = create_app()
    app.state.settings = settings
    app.state.session_factory = factory
    limiter = AccountRateLimiter(redis_url)
    app.state.account_rate_limiter = limiter
    app.state.vector_store = AsyncMock()
    files = AsyncMock(return_value=True)
    admin = {"X-API-Key": ADMIN}

    def bearer(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    try:
        with (
            patch("callobuzz_rag.accounts.email.dispatch.enqueue_email", capture),
            patch("callobuzz_rag.storage.cleanup.schedule_prefix_cleanup", files),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                body = {
                    "business_name": "Shree Labs",
                    "name": "Asha",
                    "email": "asha@example.com",
                    "password": PW,
                }
                first = await c.post("/api/v1/auth/signup", json=body)
                out["signup"] = (first.status_code, first.json())
                verified = await c.post(
                    "/api/v1/auth/verify",
                    json={"email": "asha@example.com", "code": code("asha@example.com")},
                )
                assert verified.status_code == 200, verified.text
                at = verified.json()["access_token"]
                tid = verified.json()["tenant"]["id"]
                out["verify_status"] = verified.json()["tenant"]["status"]
                out["notice"] = [(t, x) for t, x, _ in mail if t == "signup_notice"]

                out["pending_data"] = (await c.get("/api/v1/corpora", headers=bearer(at))).json()
                out["pending_whoami"] = (
                    await c.get("/api/v1/whoami", headers=bearer(at))
                ).status_code
                me = await c.get("/api/v1/tenants/me", headers=bearer(at))
                out["pending_me"] = (me.status_code, me.json().get("status"))

                listed = await c.get(
                    "/api/v1/admin/tenants",
                    params={"status": "pending", "search": "shree"},
                    headers=admin,
                )
                out["pending_list"] = listed.json()

                activated = await c.post(
                    f"/api/v1/admin/tenants/{tid}/activate",
                    json={"note": "paid via UPI"},
                    headers=admin,
                )
                out["activated"] = activated.json()
                out["active_data"] = (
                    await c.get("/api/v1/corpora", headers=bearer(at))
                ).status_code

                async with factory() as session:  # a knowledge base the hard delete removes
                    kb = Corpus(
                        name="kb",
                        tenant_id=uuid.UUID(tid),
                        qdrant_collection=f"corpus_{uuid.uuid4().hex[:8]}",
                        api_key_hash="",
                    )
                    session.add(kb)
                    await session.commit()
                    out["kb"] = (kb.id, kb.qdrant_collection)

                tk = (await c.post("/api/v1/tenants/me/rotate-key", headers=bearer(at))).json()[
                    "api_key"
                ]
                out["tk_active"] = (
                    await c.get("/api/v1/corpora", headers={"X-API-Key": tk})
                ).status_code

                sus = f"/api/v1/admin/tenants/{tid}/suspend"
                wrong = await c.post(sus, json={"confirm_name": "shree labs"}, headers=admin)
                out["suspend_wrong"] = (wrong.status_code, wrong.json().get("code"))
                right = await c.post(sus, json={"confirm_name": "Shree Labs"}, headers=admin)
                out["suspend"] = right.json()["status"]
                out["tk_suspended"] = (
                    await c.get("/api/v1/corpora", headers={"X-API-Key": tk})
                ).json()
                out["old_session"] = (await c.get("/api/v1/corpora", headers=bearer(at))).json()

                again = await c.post(
                    "/api/v1/auth/login", json={"email": "asha@example.com", "password": PW}
                )
                at2 = again.json()["access_token"]
                out["relogin_tenant_status"] = again.json()["tenant"]["status"]
                out["relogin_data"] = (
                    await c.get("/api/v1/corpora", headers=bearer(at2))
                ).status_code
                out["relogin_whoami"] = (
                    await c.get("/api/v1/whoami", headers=bearer(at2))
                ).status_code

                repeat = await c.post("/api/v1/auth/signup", json=body)
                out["signup_again"] = (repeat.status_code, repeat.json())
                out["account_exists"] = [
                    t for t, x, _ in mail if x == "asha@example.com" and t == "account_exists"
                ]

                path = f"/api/v1/admin/tenants/{tid}"
                out["delete_legacy"] = (await c.delete(path, headers=admin)).status_code
                deleted = await c.delete(path, params={"confirm_name": "Shree Labs"}, headers=admin)
                out["delete"] = deleted.status_code
                out["gone"] = (await c.get(path, headers=admin)).status_code
                out["login_after_delete"] = (
                    await c.post(
                        "/api/v1/auth/login",
                        json={"email": "asha@example.com", "password": PW},
                    )
                ).json()
                out["dropped"] = [
                    call.args[0]
                    for call in app.state.vector_store.delete_collection.await_args_list
                ]
                out["files"] = [call.args[1] for call in files.await_args_list]

                # A declined sign-up: no name needed, account removed, polite mail.
                await c.post(
                    "/api/v1/auth/signup",
                    json={**body, "business_name": "Test Co", "email": "t@example.com"},
                )
                v2 = await c.post(
                    "/api/v1/auth/verify",
                    json={"email": "t@example.com", "code": code("t@example.com")},
                )
                t2 = v2.json()["tenant"]["id"]
                out["decline"] = (
                    await c.post(f"/api/v1/admin/tenants/{t2}/decline", headers=admin)
                ).status_code
                out["declined_mail"] = [(t, x) for t, x, _ in mail if t == "declined"]
                out["login_after_decline"] = (
                    await c.post(
                        "/api/v1/auth/login", json={"email": "t@example.com", "password": PW}
                    )
                ).json()["code"]

                # Paging over admin-created tenants.
                for n in range(3):
                    await c.post("/api/v1/admin/tenants", json={"name": f"Page {n}"}, headers=admin)
                page = await c.get(
                    "/api/v1/admin/tenants",
                    params={"search": "page", "limit": 2, "offset": 1},
                    headers=admin,
                )
                out["page"] = page.json()
                bad = await c.get("/api/v1/admin/tenants", params={"limit": 501}, headers=admin)
                out["limit_501"] = bad.status_code

        async with factory() as session:
            out["kb_row"] = await session.get(Corpus, out["kb"][0])
            out["users"] = sorted(
                (await session.execute(text("SELECT email FROM users"))).scalars().all()
            )
            out["audits"] = (
                (await session.execute(text("SELECT action FROM audit_log ORDER BY created_at")))
                .scalars()
                .all()
            )
            # Cleanup: an abandoned sign-up and an old code go; a fresh one stays.
            old = datetime.now(UTC) - timedelta(days=8)
            session.add(User(email="old@example.com", name="Old", created_at=old))
            session.add(User(email="new@example.com", name="New"))
            session.add(
                EmailCode(
                    email="old@example.com",
                    purpose="verify",
                    code_hash="x" * 64,
                    expires_at=old,
                )
            )
            await session.commit()
        out["cleanup"] = await cleanup.run(factory)
        async with factory() as session:
            out["users_after_cleanup"] = sorted(
                (await session.execute(text("SELECT email FROM users"))).scalars().all()
            )
        await limiter.close()
    finally:
        await engine.dispose()
        invalidate_engine_settings_cache()
    return out


async def _sql(url: str, sql: str, **params: Any) -> list[Any]:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params)
            return list(result.all()) if result.returns_rows else []
    finally:
        await engine.dispose()


def test_signup_lifecycle_flow_and_migration() -> None:
    with PostgresContainer("postgres:16-alpine") as pg, RedisContainer(VALKEY_IMAGE) as redis:
        url = _async_url(pg)

        # --- migration: an inactive tenant becomes suspended, and back
        _alembic(url, "phase11_003")
        asyncio.run(
            _sql(
                url,
                "INSERT INTO tenants (name, is_active) VALUES ('Old On', true), ('Old Off', false)",
            )
        )
        _alembic(url, "phase11_004")
        rows = asyncio.run(
            _sql(url, "SELECT name, status, activated_at IS NOT NULL FROM tenants ORDER BY name")
        )
        assert rows == [("Old Off", "suspended", False), ("Old On", "active", True)]
        _alembic(url, "phase11_003", down=True)
        rows = asyncio.run(_sql(url, "SELECT name, is_active FROM tenants ORDER BY name"))
        assert rows == [("Old Off", False), ("Old On", True)]
        _alembic(url, "head")
        asyncio.run(_sql(url, "DELETE FROM tenants"))

        out = asyncio.run(_flow(url, _redis_url(redis)))

    assert out["signup"] == (202, {"status": "verification_sent", "email": "asha@example.com"})
    assert out["verify_status"] == "pending"
    assert out["notice"] == [("signup_notice", SA_EMAIL)]
    assert out["pending_data"] == {
        "detail": "This workspace is not active",
        "code": "workspace_not_active",
        "status": "pending",
    }
    assert out["pending_whoami"] == 200
    assert out["pending_me"] == (200, "pending")

    listed = out["pending_list"]
    assert listed["total"] == 1 and listed["count"] == 1
    assert listed["offset"] == 0 and listed["limit"] == 100
    (item,) = listed["items"]
    assert item["name"] == "Shree Labs" and item["status"] == "pending"
    assert item["owner_emails"] == ["asha@example.com"] and item["member_count"] == 1
    assert item["created_by_user_id"] is not None and item["is_active"] is False

    assert out["activated"]["status"] == "active"
    assert out["activated"]["activation_note"] == "paid via UPI"
    assert out["activated"]["activated_at"] is not None
    assert out["active_data"] == 200
    assert out["tk_active"] == 200

    assert out["suspend_wrong"] == (422, "confirm_mismatch")
    assert out["suspend"] == "suspended"
    assert out["tk_suspended"]["code"] == "workspace_not_active"
    assert out["tk_suspended"]["status"] == "suspended"
    assert out["old_session"]["code"] == "session_revoked"
    assert out["relogin_tenant_status"] == "suspended"
    assert out["relogin_data"] == 403 and out["relogin_whoami"] == 200

    assert out["signup_again"] == out["signup"]
    assert out["account_exists"] == ["account_exists"]

    assert out["delete_legacy"] == 422
    assert out["delete"] == 204 and out["gone"] == 404
    assert out["login_after_delete"]["code"] == "invalid_credentials"
    kb_id, collection = out["kb"]
    assert out["dropped"] == [collection]
    assert out["files"] == [[f"corpus/{kb_id}/", f"portal/{kb_id}/"]]
    assert out["kb_row"] is None

    assert out["decline"] == 204
    assert out["declined_mail"] == [("declined", "t@example.com")]
    assert out["login_after_decline"] == "invalid_credentials"

    page = out["page"]
    assert page["total"] == 3 and page["count"] == 2
    assert [i["name"] for i in page["items"]] == ["Page 1", "Page 0"]  # newest first
    assert out["limit_501"] == 422

    assert out["users"] == []  # both sign-up accounts went with their workspaces
    for action in (
        "tenant.signed_up",
        "tenant.activated",
        "tenant.suspended",
        "tenant.deleted",
        "tenant.declined",
        "tenant.created",
    ):
        assert action in out["audits"], action

    assert out["cleanup"]["abandoned_signups"] == 1
    assert out["cleanup"]["email_codes"] >= 1
    assert out["users_after_cleanup"] == ["new@example.com"]
