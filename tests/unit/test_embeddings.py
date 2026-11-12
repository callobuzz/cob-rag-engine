"""Unit tests for Jina embedding provider with mocked HTTP."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.embeddings.base import EmbeddingResult
from callobuzz_rag.embeddings.jina import JinaEmbeddingProvider


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.jina_api_key.get_secret_value.return_value = "test-key"
    settings.jina_model = "jina-embeddings-v3"
    settings.jina_dimensions = 1024
    settings.jina_batch_size = 64
    return settings


def _make_response(count: int = 1, dims: int = 1024):
    """Build a mock Jina API response dict."""
    return {
        "model": "jina-embeddings-v3",
        "object": "list",
        "usage": {"total_tokens": 10 * count, "prompt_tokens": 10 * count},
        "data": [
            {"object": "embedding", "index": i, "embedding": [0.1 * (i + 1)] * dims}
            for i in range(count)
        ],
    }


def _make_mock_post(response_data):
    """Create an AsyncMock for httpx client.post that returns a sync response."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = response_data
    mock_resp.raise_for_status = MagicMock()
    return AsyncMock(return_value=mock_resp)


async def test_embed_single_text(mock_settings):
    provider = JinaEmbeddingProvider(mock_settings)
    with patch.object(provider._client, "post", _make_mock_post(_make_response(1))):
        results = await provider.embed_texts(["test text"])

    assert len(results) == 1
    assert len(results[0].vector) == 1024
    assert results[0].model == "jina-embeddings-v3"
    assert results[0].dimensions == 1024


async def test_embed_multiple_texts(mock_settings):
    provider = JinaEmbeddingProvider(mock_settings)
    with patch.object(provider._client, "post", _make_mock_post(_make_response(3))):
        results = await provider.embed_texts(["a", "b", "c"])

    assert len(results) == 3
    for r in results:
        assert len(r.vector) == 1024


async def test_embed_query_uses_query_task(mock_settings):
    provider = JinaEmbeddingProvider(mock_settings)
    mock_post = _make_mock_post(_make_response(1))
    with patch.object(provider._client, "post", mock_post):
        result = await provider.embed_query("what is AI?")

    assert isinstance(result, EmbeddingResult)
    # Verify the call used retrieval.query task
    call_kwargs = mock_post.call_args
    payload = call_kwargs[1]["json"]
    assert payload["task"] == "retrieval.query"


async def test_batch_splitting(mock_settings):
    """Large input is split into batches of batch_size."""
    mock_settings.jina_batch_size = 2
    provider = JinaEmbeddingProvider(mock_settings)

    call_count = 0

    async def mock_post_fn(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        texts = kwargs.get("json", {}).get("input", [])
        resp = MagicMock()
        resp.json.return_value = _make_response(len(texts), 1024)
        resp.raise_for_status = MagicMock()
        return resp

    with patch.object(provider._client, "post", side_effect=mock_post_fn):
        results = await provider.embed_texts(["a", "b", "c", "d", "e"])

    assert len(results) == 5
    assert call_count == 3  # 2 + 2 + 1


async def test_dimension_mismatch_raises(mock_settings):
    """Dimension mismatch between config and API response raises ValueError."""
    mock_settings.jina_dimensions = 1024
    provider = JinaEmbeddingProvider(mock_settings)

    with (
        patch.object(provider._client, "post", _make_mock_post(_make_response(1, dims=512))),
        pytest.raises(ValueError, match="dimension mismatch"),
    ):
        await provider.embed_texts(["test"])


async def test_health_check_success(mock_settings):
    provider = JinaEmbeddingProvider(mock_settings)
    with patch.object(provider._client, "post", _make_mock_post(_make_response(1))):
        assert await provider.health_check() is True


async def test_health_check_failure(mock_settings):
    provider = JinaEmbeddingProvider(mock_settings)
    mock_post = AsyncMock(side_effect=Exception("connection failed"))
    with patch.object(provider._client, "post", mock_post):
        assert await provider.health_check() is False


async def test_token_count_from_usage(mock_settings):
    provider = JinaEmbeddingProvider(mock_settings)
    with patch.object(provider._client, "post", _make_mock_post(_make_response(1))):
        results = await provider.embed_texts(["test"])

    assert results[0].token_count == 10


async def test_response_ordering(mock_settings):
    """Results are returned in input order even if API returns out of order."""
    provider = JinaEmbeddingProvider(mock_settings)
    resp = {
        "model": "jina-embeddings-v3",
        "object": "list",
        "usage": {"total_tokens": 20},
        "data": [
            {"object": "embedding", "index": 1, "embedding": [0.2] * 1024},
            {"object": "embedding", "index": 0, "embedding": [0.1] * 1024},
        ],
    }
    with patch.object(provider._client, "post", _make_mock_post(resp)):
        results = await provider.embed_texts(["first", "second"])

    assert len(results) == 2
    # index 0 should come first (vector [0.1]*1024)
    assert results[0].vector[0] == pytest.approx(0.1)
    assert results[1].vector[0] == pytest.approx(0.2)
