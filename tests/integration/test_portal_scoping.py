"""Content Portal scoping against real PostgreSQL and the real key middleware (G-13).

Two tenants with a corpus each; admin creates a workspace in both, then each
tenant's keys see only their own. Requires Docker (PostgreSQL via testcontainers).
"""

import os
import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.secrets import hash_api_key
from callobuzz_rag.db.models.api_key import ApiKey

pytestmark = pytest.mark.integration

ADMIN_KEY = "integration-portal-admin-key"
P = "/api/v1/portal"


@pytest_asyncio.fixture
async def world(db_engine) -> AsyncIterator[dict[str, object]]:
    """Tenants A and B, a corpus each, and their keys (rows inserted directly)."""
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.tenant import Tenant

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    out: dict[str, object] = {}
    tenants, corpora = [], []
    async with factory() as session:
        for side in ("a", "b"):
            tenant_id, corpus_id = uuid.uuid4(), uuid.uuid4()
            tk = f"tk_{tenant_id.hex[:8]}_{uuid.uuid4().hex}"
            ck = f"ck_{corpus_id.hex[:8]}_{uuid.uuid4().hex}"
            session.add(Tenant(id=tenant_id, name=f"portal {side}"))
            await session.flush()
            session.add(
                Corpus(
                    id=corpus_id,
                    name=f"portal corpus {side}",
                    tenant_id=tenant_id,
                    qdrant_collection=f"corpus_{corpus_id.hex[:8]}",
                )
            )
            await session.flush()
            for owner, plaintext in (("tenant", tk), ("corpus", ck)):
                session.add(
                    ApiKey(
                        tenant_id=tenant_id if owner == "tenant" else None,
                        corpus_id=corpus_id if owner == "corpus" else None,
                        id_short=plaintext.split("_")[1],
                        name="Default key",
                        key_prefix=plaintext[:6],
                        key_hash=hash_api_key(plaintext),
                        permission="full",
                        is_default=True,
                    )
                )
            out.update({f"tk_{side}": tk, f"ck_{side}": ck, f"corpus_{side}": str(corpus_id)})
            tenants.append(tenant_id)
            corpora.append(corpus_id)
        await session.commit()
    yield out
    async with factory() as session:
        await session.execute(delete(Corpus).where(Corpus.id.in_(corpora)))
        await session.execute(delete(Tenant).where(Tenant.id.in_(tenants)))
        await session.commit()


def _app(db_engine):
    from callobuzz_rag.api.app import create_app

    with patch.dict(os.environ, {"RAG_ENABLE_CONTENT_PORTAL": "true"}):
        app = create_app()
    app.state.settings = Settings(
        _env_file=None, admin_api_key=ADMIN_KEY, enable_content_portal=True
    )
    app.state.session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    app.state.vector_store = AsyncMock()
    app.state.embedder = AsyncMock()
    return app


async def _call(app, key: str, method: str, path: str, **kw):
    headers = {"X-API-Key": key, **kw.pop("headers", {})}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.request(method, path, headers=headers, **kw)


async def test_each_tenant_sees_only_its_own_workspaces(db_engine, world) -> None:
    app = _app(db_engine)
    ws = {}
    for side in ("a", "b"):
        res = await _call(
            app,
            ADMIN_KEY,
            "POST",
            f"{P}/workspaces?corpus_id={world[f'corpus_{side}']}",
            json={"title": f"{side} docs"},
        )
        assert res.status_code == 201, res.text
        assert res.json()["corpus_id"] == world[f"corpus_{side}"]
        ws[side] = res.json()["id"]

    # Tenant A's key lists only A's workspaces, and naming B's corpus is refused.
    listed = await _call(app, world["tk_a"], "GET", f"{P}/workspaces")
    assert listed.status_code == 200, listed.text
    ids = {w["id"] for w in listed.json()["items"]}
    assert ws["a"] in ids
    assert ws["b"] not in ids
    other = await _call(app, world["tk_a"], "GET", f"{P}/workspaces?corpus_id={world['corpus_b']}")
    assert other.status_code == 403

    # Corpus key B cannot read A's chunks or workspace; it reads its own.
    for path in (f"{P}/workspaces/{ws['a']}/chunks", f"{P}/workspaces/{ws['a']}"):
        res = await _call(app, world["ck_b"], "GET", path)
        assert res.status_code == 403, res.text
    own = await _call(app, world["ck_b"], "GET", f"{P}/workspaces/{ws['b']}/chunks")
    assert own.status_code == 200, own.text

    # Tenant A reaches its own rules and workspace with its tenant key (was 401).
    rules = await _call(app, world["tk_a"], "GET", f"{P}/corpus/{world['corpus_a']}/rules")
    assert rules.status_code == 200, rules.text
    foreign_rules = await _call(app, world["tk_a"], "GET", f"{P}/corpus/{world['corpus_b']}/rules")
    assert foreign_rules.status_code == 403
    created = await _call(
        app,
        world["tk_a"],
        "POST",
        f"{P}/workspaces?corpus_id={world['corpus_a']}",
        json={"title": "tenant made"},
        headers={"X-User-Id": "crm-7"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["created_by"] == "crm-7"
