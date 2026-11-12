"""Accounts A5 on real PostgreSQL + Redis: the admin invites a customer's owner (link in the
response), the owner invites a member (link e-mailed), the member accepts as a new account,
is refused an owner-only route, is promoted (the same access token then works), the
original owner leaves, and the last owner cannot. Two owners demoting each other in
concurrent transactions leave exactly one owner (the row lock). Plus the phase11_005
migration up and down, the one-open-invitation index, and the cleanup of old invitations.

E-mail is captured where it is queued (`enqueue_email`); Qdrant is a stand-in. A sync
test (alembic's env.py calls asyncio.run()); the async flow runs in its own loop.
Requires Docker.
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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

from tests.integration.conftest import VALKEY_IMAGE, _async_url
from tests.integration.test_accounts_flow import _alembic, _redis_url

pytestmark = pytest.mark.integration

ADMIN = "admin-key-integration"
SA_EMAIL = "ops@example.com"
PW = "a long pass phrase"


def _token(mail: list[tuple[str, str, dict[str, Any]]], to: str) -> str:
    url = [c for t, x, c in mail if t == "invitation" and x == to][-1]["accept_url"]
    return str(url.split("#token=")[1])


async def _demote_race(factory: Any, settings: Any, tenant_id: uuid.UUID, ids: list[Any]) -> Any:
    """Two transactions, each demoting the other owner, overlapping in time."""
    from types import SimpleNamespace

    from callobuzz_rag.accounts import members
    from callobuzz_rag.api.errors import APIError
    from callobuzz_rag.db.models.tenant import Tenant

    async def demote(target: uuid.UUID) -> str:
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
            headers={},
            client=SimpleNamespace(host="203.0.113.7"),
            state=SimpleNamespace(is_admin=True, user=None, tenant=None, corpus=None),
        )
        async with factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            try:
                await members.change_role(session, tenant, target, "member", request=request)
            except APIError as exc:
                await session.rollback()
                return str(exc.code)
            await asyncio.sleep(0.3)  # hold the lock while the other one waits
            await session.commit()
            return "ok"

    return sorted(await asyncio.gather(demote(ids[0]), demote(ids[1])))


async def _flow(db_url: str, redis_url: str) -> dict[str, Any]:
    from callobuzz_rag.accounts import cleanup
    from callobuzz_rag.accounts.ratelimit import AccountRateLimiter
    from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.db.models.invitation import Invitation

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

    out: dict[str, Any] = {}
    with patch.dict(os.environ, {"RAG_ENABLE_CONTENT_PORTAL": "false"}):
        app = create_app()
    app.state.settings = settings
    app.state.session_factory = factory
    limiter = AccountRateLimiter(redis_url)
    app.state.account_rate_limiter = limiter
    app.state.vector_store = AsyncMock()
    admin = {"X-API-Key": ADMIN}

    def bearer(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    try:
        with patch("callobuzz_rag.accounts.email.dispatch.enqueue_email", capture):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                tenant = await c.post(
                    "/api/v1/admin/tenants", json={"name": "Shree Labs"}, headers=admin
                )
                tid = tenant.json()["id"]

                # The operator onboards the owner by hand: the link is in the response.
                made = await c.post(
                    f"/api/v1/admin/tenants/{tid}/invitations",
                    json={"email": "asha@example.com", "role": "owner"},
                    headers=admin,
                )
                out["admin_invite"] = made.status_code
                owner_token = made.json()["accept_url"].split("#token=")[1]
                asha = await c.post(
                    "/api/v1/invitations/accept",
                    json={"token": owner_token, "name": "Asha", "password": PW},
                )
                assert asha.status_code == 200, asha.text
                at = asha.json()["access_token"]
                asha_id = asha.json()["user"]["id"]
                out["asha_role"] = asha.json()["tenant"]["role"]

                # The owner invites a member; the link is only e-mailed.
                inv = await c.post(
                    "/api/v1/tenants/me/members",
                    json={"email": "ravi@example.com", "role": "member"},
                    headers=bearer(at),
                )
                out["owner_invite"] = (inv.status_code, "accept_url" in inv.json())
                token = _token(mail, "ravi@example.com")
                out["view"] = (
                    await c.post("/api/v1/invitations/view", json={"token": token})
                ).json()
                ravi = await c.post(
                    "/api/v1/invitations/accept",
                    json={"token": token, "name": "Ravi", "password": PW},
                )
                assert ravi.status_code == 200, ravi.text
                mt = ravi.json()["access_token"]
                ravi_id = ravi.json()["user"]["id"]
                out["reuse"] = (
                    await c.post(
                        "/api/v1/invitations/accept",
                        json={"token": token, "name": "X", "password": PW},
                    )
                ).json()

                blocked = await c.post("/api/v1/corpora", json={"name": "kb"}, headers=bearer(mt))
                out["member_create"] = (blocked.status_code, blocked.json().get("code"))
                out["member_list"] = (
                    await c.get("/api/v1/corpora", headers=bearer(mt))
                ).status_code

                promote = await c.patch(
                    f"/api/v1/tenants/me/members/{ravi_id}",
                    json={"role": "owner"},
                    headers=bearer(at),
                )
                out["promote"] = promote.json()["role"]
                created = await c.post(
                    "/api/v1/corpora", json={"name": "kb"}, headers=bearer(mt)
                )  # the same access token, now an owner's
                out["owner_create"] = created.status_code

                left = await c.delete(f"/api/v1/tenants/me/members/{asha_id}", headers=bearer(at))
                out["asha_leaves"] = left.status_code
                out["asha_after"] = (
                    (await c.get("/api/v1/whoami", headers=bearer(at))).json().get("code")
                )
                last = await c.delete(f"/api/v1/tenants/me/members/{ravi_id}", headers=bearer(mt))
                out["last_leaves"] = (last.status_code, last.json().get("code"))
                team = await c.get("/api/v1/tenants/me/members", headers=bearer(mt))
                out["team"] = [(m["email"], m["role"]) for m in team.json()["members"]]

                # A second owner, then both demote each other at the same moment.
                again = await c.post(
                    f"/api/v1/admin/tenants/{tid}/invitations",
                    json={"email": "bina@example.com", "role": "owner"},
                    headers=admin,
                )
                bina = await c.post(
                    "/api/v1/invitations/accept",
                    json={
                        "token": again.json()["accept_url"].split("#token=")[1],
                        "name": "Bina",
                        "password": PW,
                    },
                )
                bina_id = bina.json()["user"]["id"]

        out["race"] = await _demote_race(
            factory, settings, uuid.UUID(tid), [uuid.UUID(ravi_id), uuid.UUID(bina_id)]
        )
        async with factory() as session:
            out["owners_after_race"] = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM tenant_members "
                        "WHERE tenant_id = :t AND role = 'owner'"
                    ),
                    {"t": tid},
                )
            ).scalar()
            out["audits"] = sorted(
                set((await session.execute(text("SELECT action FROM audit_log"))).scalars().all())
            )

            # One open invitation per address: a second open row is refused.
            now = datetime.now(UTC)

            def row(email: str, **extra: Any) -> Invitation:
                fields: dict[str, Any] = {
                    "tenant_id": uuid.UUID(tid),
                    "email": email,
                    "role": "member",
                    "token_hash": uuid.uuid4().hex + uuid.uuid4().hex,
                    "invited_by_label": "admin",
                    "expires_at": now + timedelta(days=7),
                }
                return Invitation(**{**fields, **extra})

            session.add(row("dup@example.com"))
            await session.commit()
            session.add(row("dup@example.com"))
            try:
                await session.commit()
                out["duplicate_open"] = "accepted"
            except IntegrityError:
                await session.rollback()
                out["duplicate_open"] = "refused"
            session.add(row("dup@example.com", revoked_at=now))  # a closed one is fine
            await session.commit()

            # Cleanup: old ones go, the recent ones stay.
            old = now - timedelta(days=31)
            session.add(row("old1@example.com", expires_at=old, created_at=old))
            session.add(row("old2@example.com", revoked_at=old))
            session.add(row("old3@example.com", accepted_at=old))
            await session.commit()
            before = (await session.execute(text("SELECT count(*) FROM invitations"))).scalar()
        out["cleanup"] = await cleanup.run(factory)
        async with factory() as session:
            after = (await session.execute(text("SELECT count(*) FROM invitations"))).scalar()
            out["invitations_removed"] = int(before or 0) - int(after or 0)
        await limiter.close()
    finally:
        await engine.dispose()
        invalidate_engine_settings_cache()
    return out


async def _sql(url: str, sql: str) -> list[Any]:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql))
            return list(result.all()) if result.returns_rows else []
    finally:
        await engine.dispose()


def test_team_flow_and_migration() -> None:
    with PostgresContainer("postgres:16-alpine") as pg, RedisContainer(VALKEY_IMAGE) as redis:
        url = _async_url(pg)
        exists = "SELECT to_regclass('public.invitations') IS NOT NULL"

        _alembic(url, "phase11_004")
        assert asyncio.run(_sql(url, exists)) == [(False,)]
        _alembic(url, "phase11_005")
        assert asyncio.run(_sql(url, exists)) == [(True,)]
        indexes = asyncio.run(
            _sql(url, "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_invitations_open'")
        )
        assert "WHERE ((accepted_at IS NULL) AND (revoked_at IS NULL))" in indexes[0][0]
        _alembic(url, "phase11_004", down=True)
        assert asyncio.run(_sql(url, exists)) == [(False,)]
        _alembic(url, "head")

        out = asyncio.run(_flow(url, _redis_url(redis)))

    assert out["admin_invite"] == 201
    assert out["asha_role"] == "owner"
    assert out["owner_invite"] == (201, False)
    assert out["view"]["tenant_name"] == "Shree Labs"
    assert out["view"]["account_exists"] is False
    assert out["reuse"]["code"] == "invitation_invalid"
    assert out["member_create"] == (403, "owner_required")
    assert out["member_list"] == 200
    assert out["promote"] == "owner"
    assert out["owner_create"] == 201
    assert out["asha_leaves"] == 204
    assert out["asha_after"] == "session_revoked"
    assert out["last_leaves"] == (409, "last_owner")
    assert out["team"] == [("ravi@example.com", "owner")]
    assert out["race"] == ["last_owner", "ok"]
    assert out["owners_after_race"] == 1
    for action in (
        "member.invited",
        "invitation.accepted",
        "member.role_changed",
        "member.removed",
    ):
        assert action in out["audits"], action
    assert out["duplicate_open"] == "refused"
    assert out["cleanup"]["invitations"] == 3
    assert out["invitations_removed"] == 3
