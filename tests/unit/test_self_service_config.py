"""Tests for tenant self-service config API."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.api.app import create_app
from callobuzz_rag.core.config import Settings


def _make_settings() -> Settings:
    return Settings(
        _env_file=None,
        jina_api_key="test",
        llm_api_key="test",
        admin_api_key="test-admin-key",
        encryption_key="VHlwZV90ZXN0X2tleV8zMl9ieXRlcw==",
    )


def _make_mock_corpus(retrieval_config=None, llm_config=None, prompt_config=None):
    corpus = MagicMock()
    corpus.id = uuid.uuid4()
    corpus.name = "Test Corpus"
    corpus.description = "Test"
    corpus.qdrant_collection = f"corpus_{str(corpus.id)[:8]}"
    corpus.llm_config = llm_config
    corpus.prompt_config = prompt_config
    corpus.retrieval_config = retrieval_config
    corpus.created_at = "2026-03-08T00:00:00Z"
    return corpus


@pytest.fixture
def app():
    application = create_app()
    settings = _make_settings()
    application.state.settings = settings
    application.state.session_factory = MagicMock()
    application.state.vector_store = None
    application.state.embedder = None
    application.state.context_assembler = None
    application.state.collection_manager = None
    application.state.webhook_dispatcher = None

    # Bypass APIKeyAuthMiddleware — let deps handle auth for these tests
    from callobuzz_rag.api.auth import APIKeyAuthMiddleware

    original_dispatch = APIKeyAuthMiddleware.dispatch

    async def passthrough_dispatch(self, request, call_next):
        # Check for admin key to test admin-rejection scenario
        api_key = request.headers.get("X-API-Key", "")
        if api_key == settings.admin_api_key.get_secret_value():
            request.state.is_admin = True
            request.state.corpus = None
        else:
            request.state.is_admin = False
            request.state.corpus = None
        return await call_next(request)

    APIKeyAuthMiddleware.dispatch = passthrough_dispatch
    yield application
    APIKeyAuthMiddleware.dispatch = original_dispatch


@pytest.mark.asyncio
async def test_get_my_config(app):
    """GET /api/v1/config should return current corpus config."""
    corpus = _make_mock_corpus(
        retrieval_config={"top_k": 10, "cache_enabled": True},
        prompt_config={"system_prompt": "Hello"},
    )

    from callobuzz_rag.api.deps import scoped_corpus

    app.dependency_overrides[scoped_corpus] = lambda: corpus

    mock_session = AsyncMock()
    from callobuzz_rag.api.deps import get_session

    async def override_session():
        yield mock_session

    app.dependency_overrides[get_session] = override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/config",
            headers={"X-API-Key": "ck_fake_key"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["retrieval_config"]["top_k"] == 10
    assert data["prompt_config"]["system_prompt"] == "Hello"
    assert "llm_config" in data


@pytest.mark.asyncio
async def test_update_my_config(app):
    """PATCH /api/v1/config should update allowed config fields.

    The corpus the auth middleware supplies is DETACHED, so the handler must re-load
    it through the request's session and write to *that* instance. This test used to
    assert on the detached object and passed while the endpoint 500'd against a real
    database (BUG-7) — it is asserted here on the session-owned instance instead, and
    the two are deliberately distinct objects so the difference cannot go unnoticed.
    """
    detached = _make_mock_corpus(retrieval_config={"top_k": 10})
    managed = _make_mock_corpus(retrieval_config={"top_k": 10})
    managed.id = detached.id

    from callobuzz_rag.api.deps import scoped_corpus

    app.dependency_overrides[scoped_corpus] = lambda: detached

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.get = AsyncMock(return_value=managed)

    from callobuzz_rag.api.deps import get_session

    async def override_session():
        yield mock_session

    app.dependency_overrides[get_session] = override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/config",
            headers={"X-API-Key": "ck_fake_key"},
            json={
                "retrieval_config": {"preset": "economy"},
            },
        )

    assert resp.status_code == 200
    from callobuzz_rag.db.models.corpus import Corpus

    mock_session.get.assert_awaited_once_with(Corpus, detached.id)
    # Answer size 3, search width 10 — see the note in test_corpus_routes_retrieval.
    assert managed.retrieval_config["final_k"] == 3
    assert managed.retrieval_config["top_k"] == 10
    # The detached instance must be left alone — writing to it is the bug.
    assert detached.retrieval_config["top_k"] == 10


@pytest.mark.asyncio
async def test_update_my_config_404_when_the_corpus_is_gone(app):
    """A corpus deleted between authentication and the write is a 404, not a 500."""
    detached = _make_mock_corpus(retrieval_config={"top_k": 10})

    from callobuzz_rag.api.deps import get_session, scoped_corpus

    app.dependency_overrides[scoped_corpus] = lambda: detached

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=None)

    async def override_session():
        yield mock_session

    app.dependency_overrides[get_session] = override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/config",
            headers={"X-API-Key": "ck_fake_key"},
            json={"retrieval_config": {"top_k": 3}},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_must_name_the_corpus(app):
    """An admin key reaches any corpus's config, but has to say which one."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/config",
            headers={"X-API-Key": "test-admin-key"},
        )
    assert resp.status_code == 422
    assert "corpus_id" in resp.json()["detail"]
