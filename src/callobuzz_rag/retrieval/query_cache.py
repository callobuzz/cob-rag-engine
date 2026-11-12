"""Redis-based query response cache.

Caches full query responses (answer, citations, token_usage, etc.) in Redis.
Cache key: qcache:{corpus_id}:{sha256(normalized_query)}

Invalidation is corpus-scoped: when documents are ingested/deleted or
corpus config changes, all cached responses for that corpus are busted
via a corpus version counter.

Usage:
    cache = QueryResponseCache(settings)
    cached = await cache.get(query, corpus_id)
    if cached:
        return cached  # instant, free
    # ... run pipeline ...
    await cache.set(query, corpus_id, response_dict)
"""

import hashlib
import json
import logging
import re
import unicodedata
from typing import Any

import redis.asyncio as aioredis

from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "qcache"
VERSION_KEY_PREFIX = "qcache_ver"


class QueryResponseCache:
    """Redis cache for full query pipeline responses.

    Uses a corpus version counter for invalidation:
    - Cache key includes the corpus version number
    - When corpus data changes, version is bumped
    - Old keys naturally expire via Redis memory policies
    """

    def __init__(self, settings: Settings) -> None:
        self._redis: aioredis.Redis = aioredis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url,
            decode_responses=True,
        )

    async def get(self, query: str, corpus_id: str) -> dict[str, Any] | None:
        """Get cached response for a query+corpus pair."""
        key = await self._build_key(query, corpus_id)
        try:
            raw = await self._redis.get(key)
        except Exception:
            logger.warning("Redis GET failed for query cache", exc_info=True)
            return None

        if raw is None:
            logger.debug("Query cache MISS: %s", key[:60])
            return None

        try:
            data: dict[str, Any] = json.loads(raw)
            logger.info("Query cache HIT: %s (saved LLM call)", key[:60])
            return data
        except (json.JSONDecodeError, KeyError):
            logger.warning("Failed to deserialize cached query response")
            return None

    async def set(self, query: str, corpus_id: str, response: dict[str, Any]) -> None:
        """Cache a query response. No TTL — invalidated by version bump."""
        key = await self._build_key(query, corpus_id)
        try:
            data = json.dumps(response, default=str)
            await self._redis.set(key, data)
        except Exception:
            logger.warning("Failed to cache query response", exc_info=True)

    async def invalidate_corpus(self, corpus_id: str) -> None:
        """Invalidate all cached responses for a corpus by bumping version."""
        version_key = f"{VERSION_KEY_PREFIX}:{corpus_id}"
        try:
            new_version = await self._redis.incr(version_key)
            logger.info(
                "Query cache invalidated for corpus %s (version=%d)",
                corpus_id[:8],
                new_version,
            )
        except Exception:
            logger.warning(
                "Failed to invalidate query cache for corpus %s",
                corpus_id[:8],
                exc_info=True,
            )

    async def close(self) -> None:
        """Close the Redis connection."""
        await self._redis.aclose()

    async def _build_key(self, query: str, corpus_id: str) -> str:
        """Build cache key including corpus version for invalidation."""
        version_key = f"{VERSION_KEY_PREFIX}:{corpus_id}"
        try:
            version = await self._redis.get(version_key)
        except Exception:
            version = None
        version = version or "0"

        normalized = self._normalize_text(query)
        text_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        return f"{CACHE_KEY_PREFIX}:{corpus_id[:8]}:v{version}:{text_hash}"

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize query for consistent cache keys."""
        text = unicodedata.normalize("NFC", text)
        text = re.sub(r"\s+", " ", text.strip())
        text = text.strip(".,;:!?\"'()[]{}<>")
        return text.lower()
