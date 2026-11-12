"""A tiny JSON cache over Redis for the conversation layer (labels C1, translations C2).

Fails OPEN: a Redis error is logged at debug and treated as a miss, because both caches
only save model calls; the turn still works without them.

Build one per request or per task, never as a process-wide singleton: an asyncio Redis
client is bound to the event loop that first used it, and Celery tasks run each task in a
fresh `asyncio.run()` loop.
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class RedisJsonCache:
    """JSON values (any shape) under `prefix + key` (README P5)."""

    def __init__(self, redis_url: str, *, prefix: str = "") -> None:
        import redis.asyncio as aioredis

        self._prefix = prefix
        self._redis: Any = aioredis.from_url(  # type: ignore[no-untyped-call]
            redis_url, decode_responses=True, socket_timeout=1, socket_connect_timeout=1
        )

    @classmethod
    def from_client(cls, client: Any, *, prefix: str = "") -> "RedisJsonCache":
        instance = cls.__new__(cls)
        instance._redis = client
        instance._prefix = prefix
        return instance

    async def get(self, key: str) -> Any | None:
        full = self._prefix + key
        try:
            raw = await self._redis.get(full)
        except Exception:
            logger.debug("cache get failed for %s", full[:40], exc_info=True)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    async def set(self, key: str, value: Any, ttl_s: int) -> None:
        full = self._prefix + key
        try:
            await self._redis.set(full, json.dumps(value, ensure_ascii=False), ex=ttl_s)
        except Exception:
            logger.debug("cache set failed for %s", full[:40], exc_info=True)
