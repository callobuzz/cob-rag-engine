"""BUG-7 regression: PATCH /api/v1/config against a real database.

The endpoint 500'd on every call with
``InvalidRequestError: Instance '<Corpus>' is not persistent within this Session``.
``APIKeyAuthMiddleware`` loads the corpus in its own session and expunges it, so the
handler was mutating a *detached* instance: the write went nowhere and the trailing
``session.refresh()`` blew up. Retrieval tuning from the admin UI was impossible.

This is an integration test rather than a unit test on purpose — the defect only exists
when the auth middleware and the request session are two different real sessions, which
is precisely what a mocked session hides. It is why the bug shipped.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from callobuzz_rag.api.app import create_app
from callobuzz_rag.api.deps import get_session
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.corpus import Corpus

ADMIN_KEY = "integration-test-admin-key"


def _app(db_session: AsyncSession, db_engine):
    app = create_app()

    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    app.state.settings = Settings(_env_file=None, admin_api_key=ADMIN_KEY)
    app.state.session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    return app


async def _create_corpus(client: AsyncClient, name: str) -> tuple[str, str]:
    """Create a corpus with the admin key; return (corpus_id, corpus_api_key).

    Every corpus belongs to a tenant (accounts A1), so a tenant is created first.
    """
    resp = await client.post(
        "/api/v1/admin/tenants",
        json={"name": f"{name} tenant"},
        headers={"X-API-Key": ADMIN_KEY},
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        "/api/v1/corpora",
        json={"name": name, "description": "BUG-7 regression", "tenant_id": resp.json()["id"]},
        headers={"X-API-Key": ADMIN_KEY},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["id"], body["api_key"]


@pytest.mark.integration
async def test_patch_config_persists_and_does_not_500(db_session: AsyncSession, db_engine):
    """The whole of BUG-7: 200 instead of 500, and the value is actually written."""
    app = _app(db_session, db_engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        corpus_id, corpus_key = await _create_corpus(client, "BUG-7 Config Corpus")
        headers = {"X-API-Key": corpus_key}

        resp = await client.patch(
            "/api/v1/config",
            json={"retrieval_config": {"top_k": 7, "reranking_enabled": False}},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["retrieval_config"]["top_k"] == 7
        assert resp.json()["retrieval_config"]["reranking_enabled"] is False

        # The response echoing the value proves nothing on its own — the old code held a
        # detached object whose in-memory attributes looked right. Read the row back.
        row = (await db_session.execute(select(Corpus).where(Corpus.id == corpus_id))).scalar_one()
        await db_session.refresh(row)
        assert row.retrieval_config["top_k"] == 7
        assert row.retrieval_config["reranking_enabled"] is False

        # And GET, which reads through the middleware's own session, agrees.
        resp = await client.get("/api/v1/config", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["retrieval_config"]["top_k"] == 7


@pytest.mark.integration
async def test_patch_config_merges_rather_than_replaces(db_session: AsyncSession, db_engine):
    """A second PATCH must keep the first PATCH's untouched keys."""
    app = _app(db_session, db_engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        _, corpus_key = await _create_corpus(client, "BUG-7 Merge Corpus")
        headers = {"X-API-Key": corpus_key}

        await client.patch(
            "/api/v1/config", json={"retrieval_config": {"top_k": 9}}, headers=headers
        )
        resp = await client.patch(
            "/api/v1/config",
            json={
                "retrieval_config": {"mmr_enabled": True},
                "prompt_config": {"response_language": "en"},
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        retrieval = resp.json()["retrieval_config"]
        assert retrieval["top_k"] == 9, "second PATCH clobbered the first"
        assert retrieval["mmr_enabled"] is True
        assert resp.json()["prompt_config"]["response_language"] == "en"


@pytest.mark.integration
async def test_patch_config_rejects_the_admin_key(db_session: AsyncSession, db_engine):
    """Self-service config is corpus-scoped: an admin key must name the corpus.

    (Before dashboard epic 0 an admin key was refused outright with 403; it now
    works with `?corpus_id=` like every scoped route, and without it is a 422.)
    """
    app = _app(db_session, db_engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/config",
            json={"retrieval_config": {"top_k": 3}},
            headers={"X-API-Key": ADMIN_KEY},
        )
        assert resp.status_code == 422, resp.text
        assert "corpus_id" in resp.json()["detail"]

        resp = await client.patch("/api/v1/config", json={"retrieval_config": {"top_k": 3}})
        assert resp.status_code == 401, resp.text
