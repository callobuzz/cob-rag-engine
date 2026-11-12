"""Redis-based query embedding cache.

Caches query embedding vectors in Redis to avoid redundant API calls
for repeated queries. Cache keys include the model name so that
switching models automatically invalidates stale entries.

Usage:
    cache = QueryEmbeddingCache(settings)
    result = await cache.get_or_embed(query_text, embedding_provider, model="BAAI/bge-m3")
"""

import hashlib
import json
import logging
import re
import unicodedata

import redis.asyncio as aioredis

from callobuzz_rag.core.config import Settings
from callobuzz_rag.embeddings.base import EmbeddingProvider, EmbeddingResult

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "qemb"


class QueryEmbeddingCache:
    """Redis cache for query embedding vectors.

    Cache key format: qemb:{model}:{sha256_hex}
    Cache value: JSON-serialized EmbeddingResult fields.
    TTL: Configurable, default 3600 seconds (1 hour).
    """

    def __init__(self, settings: Settings) -> None:
        self._redis: aioredis.Redis = aioredis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url,
            decode_responses=True,
        )
        self._ttl = settings.query_embedding_cache_ttl
        self._default_model = settings.embedding_model

    async def get_or_embed(
        self,
        text: str,
        provider: EmbeddingProvider,
        model: str | None = None,
    ) -> EmbeddingResult:
        """Get cached embedding or generate and cache a new one.

        Args:
            text: Query text to embed.
            provider: Embedding provider to call on cache miss.
            model: Canonical model string for cache key. Falls back to engine default.

        Returns:
            EmbeddingResult with the query vector.
        """
        resolved_model = model or self._default_model
        if provider.query_prefix:
            # Same model, different instruction, different vector.
            resolved_model += "|q:" + hashlib.sha256(provider.query_prefix.encode()).hexdigest()[:8]
        normalized = self._normalize_text(text)
        cache_key = self._build_key(normalized, resolved_model)

        cached = await self._get_cached(cache_key)
        if cached is not None:
            logger.debug("Query embedding cache HIT: %s", cache_key)
            return cached

        logger.debug("Query embedding cache MISS: %s", cache_key)
        result = await provider.embed_query(text)

        try:
            await self._cache_result(cache_key, result)
        except Exception:
            logger.warning("Failed to cache query embedding: %s", cache_key, exc_info=True)

        return result

    async def invalidate(self, text: str, model: str | None = None) -> None:
        """Invalidate the cache entry for a specific query text."""
        resolved_model = model or self._default_model
        normalized = self._normalize_text(text)
        key = self._build_key(normalized, resolved_model)
        await self._redis.delete(key)

    async def close(self) -> None:
        """Close the Redis connection."""
        await self._redis.aclose()

    async def _get_cached(self, key: str) -> EmbeddingResult | None:
        """Retrieve a cached embedding by key."""
        try:
            raw = await self._redis.get(key)
        except Exception:
            logger.warning("Redis GET failed for %s", key, exc_info=True)
            return None

        if raw is None:
            return None

        try:
            data = json.loads(raw)
            return EmbeddingResult(
                vector=data["vector"],
                model=data["model"],
                model_version=data["model_version"],
                dimensions=data["dimensions"],
                token_count=data.get("token_count"),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to deserialize cached embedding: %s", e)
            return None

    async def _cache_result(self, key: str, result: EmbeddingResult) -> None:
        """Store an embedding result in Redis with TTL."""
        if self._ttl <= 0:
            return
        data = json.dumps(
            {
                "vector": result.vector,
                "model": result.model,
                "model_version": result.model_version,
                "dimensions": result.dimensions,
                "token_count": result.token_count,
            }
        )
        await self._redis.setex(key, self._ttl, data)

    def _build_key(self, normalized_text: str, model: str) -> str:
        """Build a deterministic cache key from normalized text and model."""
        text_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        return f"{CACHE_KEY_PREFIX}:{model}:{text_hash}"

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize query text for consistent cache keys."""
        text = unicodedata.normalize("NFC", text)
        text = re.sub(r"\s+", " ", text.strip())
        text = text.strip(".,;:!?\"'()[]{}<>")
        return text.lower()
