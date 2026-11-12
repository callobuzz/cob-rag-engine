"""Tests for model catalog API endpoints."""

from unittest.mock import MagicMock

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
    return application


@pytest.mark.asyncio
async def test_list_models(app):
    """GET /api/v1/models should return curated catalog."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/models",
            headers={"X-API-Key": "test-admin-key"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert len(data["models"]) > 0
    assert "categories" in data


@pytest.mark.asyncio
async def test_list_models_filter_by_category(app):
    """GET /api/v1/models?category=free should filter."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/models?category=free",
            headers={"X-API-Key": "test-admin-key"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert all(m["category"] == "free" for m in data["models"])


@pytest.mark.asyncio
async def test_list_presets(app):
    """GET /api/v1/models/presets should return preset definitions."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/models/presets",
            headers={"X-API-Key": "test-admin-key"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "presets" in data
    assert "count" in data
    assert data["count"] == 3
    assert "economy" in data["presets"]
    assert "standard" in data["presets"]
    assert "premium" in data["presets"]


@pytest.mark.asyncio
async def test_fast_benchmarks_serves_the_results_file(app):
    """GET /api/v1/models/fast-benchmarks returns benchmarks/results/fast-models.json."""
    import json

    from callobuzz_rag.core.benchmark_results import FAST_MODELS_PATH

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/models/fast-benchmarks", headers={"X-API-Key": "test-admin-key"}
        )
        refused = await client.get("/api/v1/models/fast-benchmarks")
    assert resp.status_code == 200 and refused.status_code == 401
    body = resp.json()
    assert body["available"] is True
    assert body["benchmarks"] == json.loads(FAST_MODELS_PATH.read_text("utf-8"))


@pytest.mark.asyncio
async def test_fast_benchmarks_without_a_file_is_empty_not_an_error(app, monkeypatch):
    from callobuzz_rag.api.routes import models as route

    monkeypatch.setattr(route, "load_fast_models", lambda: None)
    monkeypatch.setattr(route, "load_summary", lambda: None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        fast = await client.get(
            "/api/v1/models/fast-benchmarks", headers={"X-API-Key": "test-admin-key"}
        )
        summary = await client.get(
            "/api/v1/models/benchmark-summary", headers={"X-API-Key": "test-admin-key"}
        )
    assert fast.json() == {"available": False, "benchmarks": None}
    assert summary.json() == {"available": False, "summary": None}


@pytest.mark.asyncio
async def test_benchmark_summary(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/models/benchmark-summary", headers={"X-API-Key": "test-admin-key"}
        )
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()["summary"]["sections"]]
    assert ids == ["beir", "documents", "labels", "ttft"]


@pytest.mark.asyncio
async def test_answer_benchmarks_serves_the_normalised_table(app, monkeypatch):
    from callobuzz_rag.core.answer_benchmarks import load_answer_models

    load_answer_models.cache_clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/models/answer-benchmarks", headers={"X-API-Key": "test-admin-key"}
        )
        refused = await client.get("/api/v1/models/answer-benchmarks")
    assert resp.status_code == 200 and refused.status_code == 401
    body = resp.json()
    assert body["available"] is True
    assert body["benchmarks"]["recommendations"]["default"] == "openai/gpt-oss-120b|low"

    from callobuzz_rag.api.routes import models as route

    monkeypatch.setattr(route, "load_answer_models", lambda: None)
    monkeypatch.setattr(route, "load_local_benchmarks", lambda: None)  # PIP2
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        empty = await client.get(
            "/api/v1/models/answer-benchmarks", headers={"X-API-Key": "test-admin-key"}
        )
    assert empty.json() == {"available": False, "benchmarks": None, "local": None}
