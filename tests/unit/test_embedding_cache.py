"""Unit tests for query embedding cache."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.embeddings.base import EmbeddingResult
from callobuzz_rag.retrieval.embedding_cache import (
    CACHE_KEY_PREFIX,
    QueryEmbeddingCache,
)


@pytest.fixture
def mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.redis_url = "redis://localhost:6380/0"
    settings.query_embedding_cache_ttl = 3600
    settings.embedding_model = "BAAI/bge-m3"
    return settings


@pytest.fixture
def sample_result() -> EmbeddingResult:
    return EmbeddingResult(
        vector=[0.1] * 1024,
        model="BAAI/bge-m3",
        model_version="BAAI/bge-m3",
        dimensions=1024,
        token_count=5,
    )


class TestCacheKeyBuilding:
    def test_key_format(self, mock_settings: MagicMock) -> None:
        cache = QueryEmbeddingCache(mock_settings)
        key = cache._build_key("hello world", "BAAI/bge-m3")
        assert key.startswith(f"{CACHE_KEY_PREFIX}:BAAI/bge-m3:")
        parts = key.split(":")
        assert len(parts) == 3
        assert len(parts[2]) == 64

    def test_deterministic(self, mock_settings: MagicMock) -> None:
        cache = QueryEmbeddingCache(mock_settings)
        key1 = cache._build_key("hello world", "BAAI/bge-m3")
        key2 = cache._build_key("hello world", "BAAI/bge-m3")
        assert key1 == key2

    def test_different_text_different_key(self, mock_settings: MagicMock) -> None:
        cache = QueryEmbeddingCache(mock_settings)
        key1 = cache._build_key("hello world", "BAAI/bge-m3")
        key2 = cache._build_key("goodbye world", "BAAI/bge-m3")
        assert key1 != key2

    def test_model_in_key(self, mock_settings: MagicMock) -> None:
        cache = QueryEmbeddingCache(mock_settings)
        key = cache._build_key("test", "jina-embeddings-v3")
        assert "jina-embeddings-v3" in key

    def test_different_model_different_key(self, mock_settings: MagicMock) -> None:
        cache = QueryEmbeddingCache(mock_settings)
        key1 = cache._build_key("test", "BAAI/bge-m3")
        key2 = cache._build_key("test", "jina-embeddings-v3")
        assert key1 != key2


class TestNormalization:
    def test_case_insensitive(self) -> None:
        n1 = QueryEmbeddingCache._normalize_text("Hello World")
        n2 = QueryEmbeddingCache._normalize_text("hello world")
        assert n1 == n2

    def test_whitespace_collapse(self) -> None:
        n1 = QueryEmbeddingCache._normalize_text("hello   world")
        n2 = QueryEmbeddingCache._normalize_text("hello world")
        assert n1 == n2

    def test_strip(self) -> None:
        result = QueryEmbeddingCache._normalize_text("  hello  ")
        assert result == "hello"


class TestGetOrEmbed:
    @pytest.mark.asyncio
    async def test_cache_miss_calls_provider(
        self,
        mock_settings: MagicMock,
        sample_result: EmbeddingResult,
    ) -> None:
        cache = QueryEmbeddingCache(mock_settings)

        cache._redis = AsyncMock()
        cache._redis.get = AsyncMock(return_value=None)
        cache._redis.setex = AsyncMock()

        provider = AsyncMock(query_prefix="")
        provider.embed_query = AsyncMock(return_value=sample_result)

        result = await cache.get_or_embed("test query", provider)

        assert result == sample_result
        provider.embed_query.assert_called_once_with("test query")
        cache._redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_hit_skips_provider(
        self,
        mock_settings: MagicMock,
        sample_result: EmbeddingResult,
    ) -> None:
        cache = QueryEmbeddingCache(mock_settings)

        cached_json = json.dumps(
            {
                "vector": sample_result.vector,
                "model": sample_result.model,
                "model_version": sample_result.model_version,
                "dimensions": sample_result.dimensions,
                "token_count": sample_result.token_count,
            }
        )
        cache._redis = AsyncMock()
        cache._redis.get = AsyncMock(return_value=cached_json)

        provider = AsyncMock(query_prefix="")
        result = await cache.get_or_embed("test query", provider)

        assert result.vector == sample_result.vector
        assert result.model == sample_result.model
        provider.embed_query.assert_not_called()

    @pytest.mark.asyncio
    async def test_ttl_zero_skips_cache_write(
        self,
        sample_result: EmbeddingResult,
    ) -> None:
        settings = MagicMock()
        settings.redis_url = "redis://localhost:6380/0"
        settings.query_embedding_cache_ttl = 0
        settings.embedding_model = "BAAI/bge-m3"
        cache = QueryEmbeddingCache(settings)
        cache._redis = AsyncMock()
        cache._redis.get = AsyncMock(return_value=None)
        cache._redis.setex = AsyncMock()

        provider = AsyncMock(query_prefix="")
        provider.embed_query = AsyncMock(return_value=sample_result)

        result = await cache.get_or_embed("test query", provider)
        assert result == sample_result
        cache._redis.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_per_corpus_model_overrides_default(
        self,
        mock_settings: MagicMock,
        sample_result: EmbeddingResult,
    ) -> None:
        """Passing model= to get_or_embed uses that model for cache key."""
        cache = QueryEmbeddingCache(mock_settings)
        cache._redis = AsyncMock()
        cache._redis.get = AsyncMock(return_value=None)
        cache._redis.setex = AsyncMock()

        provider = AsyncMock(query_prefix="")
        provider.embed_query = AsyncMock(return_value=sample_result)

        await cache.get_or_embed("test query", provider, model="jina-embeddings-v3")

        call_args = cache._redis.setex.call_args
        key = call_args[0][0]
        assert "jina-embeddings-v3" in key
        assert "BAAI/bge-m3" not in key

    @pytest.mark.asyncio
    async def test_query_prefix_is_part_of_the_key(
        self,
        mock_settings: MagicMock,
        sample_result: EmbeddingResult,
    ) -> None:
        """Changing the query instruction must not serve vectors made without it."""
        cache = QueryEmbeddingCache(mock_settings)
        cache._redis = AsyncMock()
        cache._redis.get = AsyncMock(return_value=None)
        cache._redis.setex = AsyncMock()

        keys = []
        for prefix in ("", "Represent this sentence for searching relevant passages: "):
            provider = AsyncMock(query_prefix=prefix)
            provider.embed_query = AsyncMock(return_value=sample_result)
            await cache.get_or_embed("test query", provider)
            keys.append(cache._redis.setex.call_args[0][0])
        assert keys[0] != keys[1]

    @pytest.mark.asyncio
    async def test_invalidate_removes_key(self, mock_settings: MagicMock) -> None:
        cache = QueryEmbeddingCache(mock_settings)
        cache._redis = AsyncMock()
        cache._redis.delete = AsyncMock()

        await cache.invalidate("test query")
        cache._redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_shuts_down_redis(self, mock_settings: MagicMock) -> None:
        cache = QueryEmbeddingCache(mock_settings)
        cache._redis = AsyncMock()
        cache._redis.aclose = AsyncMock()

        await cache.close()
        cache._redis.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_redis_failure_falls_through(
        self,
        mock_settings: MagicMock,
        sample_result: EmbeddingResult,
    ) -> None:
        cache = QueryEmbeddingCache(mock_settings)

        cache._redis = AsyncMock()
        cache._redis.get = AsyncMock(side_effect=ConnectionError("Redis down"))
        cache._redis.setex = AsyncMock(side_effect=ConnectionError("Redis down"))

        provider = AsyncMock(query_prefix="")
        provider.embed_query = AsyncMock(return_value=sample_result)

        result = await cache.get_or_embed("test query", provider)
        assert result == sample_result
        provider.embed_query.assert_called_once()
