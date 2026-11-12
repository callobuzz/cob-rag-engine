"""Integration test for Corpus API against real PostgreSQL.

Requires Docker (PostgreSQL via testcontainers).
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from callobuzz_rag.api.app import create_app
from callobuzz_rag.api.deps import get_session
from callobuzz_rag.core.config import Settings

ADMIN_KEY = "integration-test-admin-key"


def _app_with_session(db_session: AsyncSession, db_engine):
    """Build the real app, pointed at the testcontainer session and a known admin key.

    Auth is exercised for real through APIKeyAuthMiddleware rather than overridden —
    the admin branch only needs `settings.admin_api_key`, so a real request with the
    header takes the same path production does. `session_factory` is real too, because
    the non-admin key branches open their own session to scan tenant/corpus hashes.
    """
    app = create_app()

    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    app.state.settings = Settings(_env_file=None, admin_api_key=ADMIN_KEY)
    app.state.session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    return app


@pytest.mark.integration
async def test_create_and_list_corpora(db_session: AsyncSession, db_engine):
    """Full API roundtrip: create corpus, list corpora, get by ID, delete."""
    app = _app_with_session(db_session, db_engine)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": ADMIN_KEY},
    ) as client:
        # 1. Health check
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["services"]["api"] is True

        # 2. Create corpus: every corpus belongs to a tenant, so create one first.
        resp = await client.post(
            "/api/v1/corpora", json={"name": "No Tenant", "description": "refused"}
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "tenant_required"
        resp = await client.post("/api/v1/admin/tenants", json={"name": "Integration Tenant"})
        assert resp.status_code == 201, resp.text
        tenant_id = resp.json()["id"]
        resp = await client.post(
            "/api/v1/corpora",
            json={
                "name": "Integration Test Corpus",
                "description": "Created by integration test",
                "tenant_id": tenant_id,
            },
        )
        assert resp.status_code == 201, resp.text
        corpus = resp.json()
        assert corpus["name"] == "Integration Test Corpus"
        corpus_id = corpus["id"]

        # 3. List corpora — should contain our corpus
        resp = await client.get("/api/v1/corpora")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        names = [c["name"] for c in data["items"]]
        assert "Integration Test Corpus" in names

        # 4. Get by ID
        resp = await client.get(f"/api/v1/corpora/{corpus_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == corpus_id

        # 5. Delete: with an API key the exact name is required (the delete guardrail)
        resp = await client.delete(f"/api/v1/corpora/{corpus_id}")
        assert resp.status_code == 422
        assert resp.json()["code"] == "confirmation_required"
        resp = await client.get(f"/api/v1/corpora/{corpus_id}/delete-preview")
        assert resp.status_code == 200
        assert resp.json()["confirm_name"] == "Integration Test Corpus"
        resp = await client.delete(
            f"/api/v1/corpora/{corpus_id}", params={"confirm_name": "Integration Test Corpus"}
        )
        assert resp.status_code == 204

        # 6. Verify deleted
        resp = await client.get(f"/api/v1/corpora/{corpus_id}")
        assert resp.status_code == 404


@pytest.mark.integration
async def test_corpus_routes_reject_unauthenticated_callers(db_session: AsyncSession, db_engine):
    """The gate is real: no key and a wrong key both fail, and neither creates a corpus.

    This is the assertion that was missing when the roundtrip test above silently
    started returning 401 — a suite that never asserts the unauthenticated case cannot
    tell "auth works" from "every request is refused".
    """
    app = _app_with_session(db_session, db_engine)
    transport = ASGITransport(app=app)
    body = {"name": "Should Never Exist", "description": "unauthenticated"}

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.post("/api/v1/corpora", json=body)).status_code == 401
        assert (await client.get("/api/v1/corpora")).status_code == 401

    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-API-Key": "not-the-admin-key"}
    ) as client:
        assert (await client.post("/api/v1/corpora", json=body)).status_code == 401

    # And nothing was written.
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-API-Key": ADMIN_KEY}
    ) as client:
        resp = await client.get("/api/v1/corpora")
        assert resp.status_code == 200
        assert "Should Never Exist" not in [c["name"] for c in resp.json()["items"]]
