"""GET /api/v1/admin/audit on real PostgreSQL (accounts A8): tenant, exact action, action
prefix, actor substring (case-insensitive, LIKE wildcards literal), time range, newest
first, paging and the total.

A sync test (alembic's env.py calls asyncio.run()); the async part runs in its own loop.
Requires Docker.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import _async_url
from tests.integration.test_accounts_flow import _alembic

pytestmark = pytest.mark.integration

ADMIN = "admin-key-integration"


async def _flow(db_url: str) -> dict[str, Any]:
    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.db.models.audit_log import AuditLog

    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    shree, pune = uuid.uuid4(), uuid.uuid4()
    rows = [
        # (days ago, action, tenant, actor label)
        (1, "tenant.signed_up", shree, "asha@shree.example"),
        (2, "tenant.activated", shree, "admin key"),
        (3, "member.invited", shree, "Asha_Owner%"),
        (4, "tenant.suspended", pune, "ops@operator.example"),
        (5, "plan.created", None, "admin key"),
        (200, "tenant.deleted", pune, "admin key"),  # outside the default 90 days
    ]
    async with factory() as session:
        for days, action, tenant, label in rows:
            session.add(
                AuditLog(
                    created_at=now - timedelta(days=days),
                    actor_kind="admin_key",
                    actor_label=label,
                    action=action,
                    tenant_id=tenant,
                    tenant_label="x" if tenant else None,
                    details={"n": days},
                )
            )
        await session.commit()

    with patch.dict(os.environ, {"RAG_ENABLE_CONTENT_PORTAL": "false"}):
        app = create_app()
    app.state.settings = Settings(_env_file=None, admin_api_key=ADMIN)  # type: ignore[call-arg,arg-type]
    app.state.session_factory = factory
    out: dict[str, Any] = {}

    async def get(**params: Any) -> Any:
        res = await c.get("/api/v1/admin/audit", params=params, headers={"X-API-Key": ADMIN})
        assert res.status_code == 200, res.text
        return res.json()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            out["all"] = await get()
            out["page"] = await get(limit=2, offset=1)
            out["shree"] = await get(tenant_id=str(shree))
            out["exact"] = await get(action="tenant.activated")
            out["prefix"] = await get(action="tenant.")
            out["actor"] = await get(actor="ADMIN")
            out["literal"] = await get(actor="_owner%")
            out["wildcard_is_literal"] = await get(actor="a%a")
            out["year"] = await get(
                start=(now - timedelta(days=300)).isoformat(), end=now.isoformat()
            )
            out["no_key"] = (await c.get("/api/v1/admin/audit")).status_code
    finally:
        await engine.dispose()
    return out


def _actions(page: dict[str, Any]) -> list[str]:
    return [i["action"] for i in page["items"]]


def test_audit_list_filters_on_postgres() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")
        out = asyncio.run(_flow(url))

    newest_first = [
        "tenant.signed_up",
        "tenant.activated",
        "member.invited",
        "tenant.suspended",
        "plan.created",
    ]
    assert _actions(out["all"]) == newest_first and out["all"]["total"] == 5
    assert _actions(out["page"]) == newest_first[1:3]
    assert out["page"]["total"] == 5 and out["page"]["limit"] == 2 and out["page"]["offset"] == 1
    assert _actions(out["shree"]) == newest_first[:3]
    assert _actions(out["exact"]) == ["tenant.activated"]
    assert _actions(out["prefix"]) == ["tenant.signed_up", "tenant.activated", "tenant.suspended"]
    assert _actions(out["actor"]) == ["tenant.activated", "plan.created"]
    assert _actions(out["literal"]) == ["member.invited"]
    assert out["wildcard_is_literal"]["total"] == 0
    assert out["year"]["total"] == 6 and _actions(out["year"])[-1] == "tenant.deleted"
    assert out["all"]["items"][0]["details"] == {"n": 1}
    assert out["no_key"] == 401
