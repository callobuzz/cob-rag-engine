"""The translated query variant: skipped when not needed, cached, fails open."""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from callobuzz_rag.conversation.cache import RedisJsonCache
from callobuzz_rag.conversation.translate import translate_for_retrieval
from callobuzz_rag.conversation.types import LanguageInfo, Script
from callobuzz_rag.core.config import Settings
from callobuzz_rag.llm.models import LLMResponse

HINGLISH = LanguageInfo("hi-Latn", Script.LATIN, 0.9)


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.data[key] = value
        return True


def _llm(reply: str = '{"query": "Do you collect samples at home in Kothrud?"}') -> Any:
    llm = MagicMock()
    llm.has_credentials = True
    llm.fast_call_options = MagicMock(return_value={"model": "fast"})
    llm.complete_json = AsyncMock(return_value=LLMResponse(content=reply, model="fast"))
    return llm


async def test_translates_and_caches() -> None:
    redis = FakeRedis()
    cache = RedisJsonCache.from_client(redis, prefix="atrans:v1:")
    llm = _llm()
    text = "Kothrud mein home collection hota hai?"
    out = await translate_for_retrieval(llm, text, HINGLISH, "en", cache=cache, timeout_s=1.5)
    assert out == "Do you collect samples at home in Kothrud?"
    again = await translate_for_retrieval(llm, text, HINGLISH, "en", cache=cache, timeout_s=1.5)
    assert again == out
    assert llm.complete_json.await_count == 1
    key = next(iter(redis.data))
    assert key.startswith("atrans:v1:") and json.loads(redis.data[key]) == {"query": out}


async def test_the_provider_timeout_is_capped_so_its_cost_is_recorded() -> None:
    llm = _llm()
    llm.fast_call_options = MagicMock(return_value={"model": "fast", "timeout": 30.0})
    await translate_for_retrieval(llm, "kab khulta hai", HINGLISH, "en", cache=None, timeout_s=1.5)
    assert llm.complete_json.await_args.kwargs["timeout"] == 1.5
    assert llm.complete_json.await_args.kwargs["model"] == "fast"


async def test_same_language_or_no_credentials_skips() -> None:
    llm = _llm()
    english = LanguageInfo("en", Script.LATIN, 0.9)
    assert (
        await translate_for_retrieval(llm, "timings?", english, "en", cache=None, timeout_s=1)
        is None
    )
    llm.has_credentials = False
    assert (
        await translate_for_retrieval(
            llm, "kab khulta hai", HINGLISH, "en", cache=None, timeout_s=1
        )
        is None
    )
    llm.complete_json.assert_not_awaited()


async def test_bad_json_timeout_and_echo_fail_open() -> None:
    text = "kab khulta hai"
    bad = _llm("not json")
    assert await translate_for_retrieval(bad, text, HINGLISH, "en", cache=None, timeout_s=1) is None
    echo = _llm('{"query": "kab khulta hai"}')
    assert (
        await translate_for_retrieval(echo, text, HINGLISH, "en", cache=None, timeout_s=1) is None
    )

    slow = _llm()

    async def hang(*_a: Any, **_k: Any) -> LLMResponse:
        await asyncio.sleep(5)
        return LLMResponse(content="{}", model="fast")

    slow.complete_json = AsyncMock(side_effect=hang)
    assert (
        await translate_for_retrieval(slow, text, HINGLISH, "en", cache=None, timeout_s=0.05)
        is None
    )


async def test_cache_fails_open() -> None:
    broken = MagicMock()
    broken.get = AsyncMock(side_effect=ConnectionError("down"))
    broken.set = AsyncMock(side_effect=ConnectionError("down"))
    cache = RedisJsonCache.from_client(broken)
    assert await cache.get("k") is None
    await cache.set("k", {"a": 1}, 10)


async def test_cache_prefix_and_any_json_value() -> None:
    redis = FakeRedis()
    cache = RedisJsonCache.from_client(redis, prefix="alabel:v1:")
    await cache.set("abc", ["business_question", 0.9], 60)
    assert list(redis.data) == ["alabel:v1:abc"]
    assert await cache.get("abc") == ["business_question", 0.9]
    assert await cache.get("missing") is None


def test_settings_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.assistant_default_for_new_kbs is True
    assert s.assistant_translate_timeout_ms == 1500
