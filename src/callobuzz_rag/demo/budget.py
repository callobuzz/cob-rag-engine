"""A hard daily ceiling on model calls in a demo (`RAG_DEMO_MODEL_CALLS_PER_DAY`).

One Redis counter per demo day (it starts at `RAG_DEMO_RESET_AT_UTC`, see `day.py`). It sits under the fair-use limits the seed sets (the demo plan's
questions per day, the engine's AI-spend ceiling), which give a visitor the normal "paused"
reply first; this is the backstop for paths fair use does not meter (the operator's playground,
setup generation, rule suggestions, COB, the agent loop). It fails closed: when Redis cannot
answer, the call is refused, because an unmetered demo would spend without a limit.

`check_model_call` is called at the single funnel `LiteLLMProvider._send` and before the three
direct `litellm.acompletion` calls (`agent/loop.py` twice, `content_portal/services/ai_transformer.py`).
The free local model (`ollama_chat`) costs nothing and is not counted.
"""

import asyncio
import logging
import weakref
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final

from callobuzz_rag.core.exceptions import LLMError
from callobuzz_rag.demo import mode
from callobuzz_rag.demo.day import day_key

logger = logging.getLogger(__name__)

KEY_PREFIX: Final = "demo:model_calls:"
TTL_S: Final = 172_800
LOCAL_PROVIDER: Final = "ollama_chat"


class DemoBudgetExceeded(LLMError):  # noqa: N818 - named in the epic; an LLMError like its siblings
    """The demo has used today's model calls."""


class DemoBudgetUnavailable(LLMError):  # noqa: N818
    """The counter could not be read (Redis down): refused, never unmetered."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DemoBudget:
    def __init__(
        self,
        redis: Any,
        limit: int,
        now_fn: Callable[[], datetime] = _utc_now,
        reset_at: str = "00:00",
    ) -> None:
        self._redis = redis
        self._limit = limit
        self._now = now_fn
        self._reset_at = reset_at

    def key(self) -> str:
        return f"{KEY_PREFIX}{day_key(self._now(), self._reset_at).isoformat()}"

    async def spend(self) -> None:
        """Count one model call.

        Raises:
            DemoBudgetExceeded: this call is over today's limit.
            DemoBudgetUnavailable: Redis did not answer.
        """
        key = self.key()
        try:
            pipe = self._redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, TTL_S, nx=True)
            count, _ = await pipe.execute()
        except Exception as exc:
            logger.warning("Demo model budget unavailable: %s", type(exc).__name__)
            raise DemoBudgetUnavailable(
                "The demo cannot count model calls right now, so it is not answering."
            ) from exc
        if int(count) > self._limit:
            raise DemoBudgetExceeded(
                "The demo has used all of today's AI answers. It starts again tomorrow."
            )

    async def used_today(self) -> int | None:
        """Calls counted so far today; None when Redis does not answer."""
        try:
            value = await self._redis.get(self.key())
        except Exception:
            return None
        return int(value) if value is not None else 0

    @property
    def limit(self) -> int:
        return self._limit


# One client per event loop: Celery tasks run `asyncio.run` per task, and a redis.asyncio
# client is bound to the loop it first used.
_budgets: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, DemoBudget]" = (
    weakref.WeakKeyDictionary()
)


def _demo_on() -> bool:
    return mode.is_demo()


def _budget() -> DemoBudget:
    loop = asyncio.get_running_loop()
    existing = _budgets.get(loop)
    if existing is not None:
        return existing
    import redis.asyncio as aioredis

    from callobuzz_rag.core.config import get_settings

    settings = get_settings()
    client = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, socket_timeout=2, socket_connect_timeout=2
    )
    created = DemoBudget(
        client, settings.demo_model_calls_per_day, reset_at=settings.demo_reset_at_utc
    )
    _budgets[loop] = created
    return created


async def check_model_call(provider: str | None, model: str | None = None) -> None:
    """Count one model call in a demo; free when demo mode is off or the model is local."""
    if not _demo_on():
        return
    if provider == LOCAL_PROVIDER or (model or "").startswith(f"{LOCAL_PROVIDER}/"):
        return
    # The turn's answer (a demo visitor's first model call of a turn): the per-visitor and the
    # whole-demo limit. It runs first so a refused answer does not spend a call.
    from callobuzz_rag.demo import limits

    scope = limits.current_scope()
    if scope is not None:
        await scope.count()
    await _budget().spend()


async def used_today_from_settings() -> int | None:
    """Calls counted today by this process's client (None when Redis does not answer)."""
    return await _budget().used_today()
