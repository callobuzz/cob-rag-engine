"""Tests for OpenRouter live model discovery endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

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
async def test_discover_openrouter_endpoint(app):
    """POST /api/v1/models/discover/openrouter should call OpenRouter API."""
    mock_models = [
        {
            "id": "openrouter/test/model-1",
            "name": "Test Model",
            "provider": "openrouter",
            "category": "budget",
            "input_cost_per_1m": 0.5,
            "output_cost_per_1m": 1.0,
            "context_window": 8192,
            "supports_streaming": True,
        }
    ]

    with patch(
        "callobuzz_rag.api.routes.models.discover_openrouter_models",
        new_callable=AsyncMock,
        return_value=mock_models,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/models/discover/openrouter",
                headers={
                    "X-API-Key": "test-admin-key",
                    "X-OpenRouter-Key": "or-test-key",
                },
            )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["models"]) == 1
    assert data["models"][0]["id"] == "openrouter/test/model-1"


@pytest.mark.asyncio
async def test_discover_openrouter_without_key(app):
    """POST /api/v1/models/discover/openrouter without X-OpenRouter-Key should 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/models/discover/openrouter",
            headers={"X-API-Key": "test-admin-key"},
        )
    assert resp.status_code == 422
