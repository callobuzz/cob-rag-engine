"""Tests for corpus CRUD with retrieval_config."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.core.config import Settings


def _make_settings() -> Settings:
    return Settings(
        _env_file=None,
        jina_api_key="test",
        llm_api_key="test",
        admin_api_key="test-admin-key",
        encryption_key="VHlwZV90ZXN0X2tleV8zMl9ieXRlcw==",
    )


def _make_mock_corpus(retrieval_config=None):
    corpus = MagicMock()
    corpus.id = uuid.uuid4()
    corpus.name = "Test"
    corpus.description = "Test corpus"
    corpus.qdrant_collection = f"corpus_{str(corpus.id)[:8]}"
    corpus.tenant_id = uuid.uuid4()
    corpus.api_key_hash = "hash"
    corpus.llm_config = None
    corpus.prompt_config = None
    corpus.retrieval_config = retrieval_config
    corpus.embedding_config = None
    corpus.applied_embedding = None
    corpus.created_at = "2026-03-08T00:00:00Z"
    return corpus


@pytest.fixture(autouse=True)
def _no_corpus_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests mock the session per route; the stats query is tested on its own."""
    from unittest.mock import AsyncMock as _AsyncMock

    monkeypatch.setattr("callobuzz_rag.api.routes.corpus.corpus_stats", _AsyncMock(return_value={}))


@pytest.fixture
def app():
    from callobuzz_rag.api.app import create_app

    application = create_app()
    settings = _make_settings()
    application.state.settings = settings
    application.state.session_factory = MagicMock()
    application.state.vector_store = None
    application.state.embedder = None
    application.state.context_assembler = None
    application.state.collection_manager = None
    application.state.webhook_dispatcher = None
    return application


@pytest.mark.asyncio
async def test_get_corpus_returns_retrieval_config(app):
    """GET /corpora/{id} should return retrieval_config in response."""
    corpus = _make_mock_corpus(retrieval_config={"top_k": 10, "cache_enabled": True})

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=corpus)

    async def override_session():
        yield mock_session

    from callobuzz_rag.api.deps import get_session

    app.dependency_overrides[get_session] = override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            f"/api/v1/corpora/{corpus.id}",
            headers={"X-API-Key": "test-admin-key"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "retrieval_config" in data
    assert data["retrieval_config"]["top_k"] == 10


@pytest.mark.asyncio
async def test_update_corpus_with_preset(app):
    """PUT /corpora/{id} with preset should expand and store retrieval_config."""
    corpus = _make_mock_corpus()

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=corpus)
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    async def override_session():
        yield mock_session

    from callobuzz_rag.api.deps import get_session

    app.dependency_overrides[get_session] = override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            f"/api/v1/corpora/{corpus.id}",
            headers={"X-API-Key": "test-admin-key"},
            json={"retrieval_config": {"preset": "economy"}},
        )

    assert resp.status_code == 200
    assert corpus.retrieval_config is not None
    # Economy is the cheap tier, so it ANSWERS with 3 chunks — but it still
    # searches wider than that. Asserting `top_k == 3` here would re-encode the
    # conflation that pinned a live corpus to a 5-in-5-out funnel.
    assert corpus.retrieval_config["final_k"] == 3
    assert corpus.retrieval_config["top_k"] == 10
    assert corpus.retrieval_config["validation_enabled"] is False


@pytest.mark.asyncio
async def test_update_corpus_with_preset_and_overrides(app):
    """Preset + individual overrides: overrides win."""
    corpus = _make_mock_corpus()

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=corpus)
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()

    async def override_session():
        yield mock_session

    from callobuzz_rag.api.deps import get_session

    app.dependency_overrides[get_session] = override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            f"/api/v1/corpora/{corpus.id}",
            headers={"X-API-Key": "test-admin-key"},
            json={"retrieval_config": {"preset": "economy", "top_k": 15}},
        )

    assert resp.status_code == 200
    assert corpus.retrieval_config["top_k"] == 15
    assert corpus.retrieval_config["validation_enabled"] is False
