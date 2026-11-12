from datetime import UTC, datetime
from typing import Any

import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import LLMError
from callobuzz_rag.demo import budget
from tests.unit.accounts_fakes import FakeRedis

NOW = datetime(2026, 10, 7, 23, 59, tzinfo=UTC)


async def test_counts_and_stops_at_the_limit() -> None:
    redis = FakeRedis()
    b = budget.DemoBudget(redis, limit=3, now_fn=lambda: NOW)
    for _ in range(3):
        await b.spend()
    with pytest.raises(budget.DemoBudgetExceeded) as err:
        await b.spend()
    assert isinstance(err.value, LLMError)
    assert redis.values["demo:model_calls:2026-10-07"] == 4
    assert await redis.ttl("demo:model_calls:2026-10-07") == 172_800


async def test_a_new_utc_day_starts_again() -> None:
    redis = FakeRedis()
    now = [NOW]
    b = budget.DemoBudget(redis, limit=1, now_fn=lambda: now[0])
    await b.spend()
    now[0] = datetime(2026, 10, 8, 0, 0, tzinfo=UTC)
    await b.spend()


async def test_fails_closed_when_redis_is_down() -> None:
    redis = FakeRedis()
    redis.down = True
    with pytest.raises(budget.DemoBudgetUnavailable):
        await budget.DemoBudget(redis, limit=10, now_fn=lambda: NOW).spend()


async def test_used_today() -> None:
    redis = FakeRedis()
    b = budget.DemoBudget(redis, limit=10, now_fn=lambda: NOW)
    assert await b.used_today() == 0
    await b.spend()
    await b.spend()
    assert await b.used_today() == 2
    redis.down = True
    assert await b.used_today() is None


async def test_check_model_call_is_free_when_off_and_for_the_local_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _B:
        async def spend(self) -> None:
            calls.append("x")

    monkeypatch.setattr(budget, "_budget", lambda: _B())
    monkeypatch.setattr(budget, "_demo_on", lambda: False)
    await budget.check_model_call("openrouter")
    monkeypatch.setattr(budget, "_demo_on", lambda: True)
    await budget.check_model_call("ollama_chat")
    await budget.check_model_call(None, "ollama_chat/qwen3:4b")
    assert calls == []
    await budget.check_model_call("openrouter")
    await budget.check_model_call(None, "openrouter/google/gemini-2.5-flash")
    assert calls == ["x", "x"]


async def test_the_provider_checks_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider

    async def _refuse(provider: str | None, model: str | None = None) -> None:
        raise budget.DemoBudgetExceeded("demo budget")

    monkeypatch.setattr(budget, "check_model_call", _refuse)
    sent: list[object] = []

    class _Lite:
        async def acompletion(self, **kw: object) -> object:
            sent.append(kw)
            return {}

    provider = LiteLLMProvider(Settings())
    with pytest.raises(LLMError):
        await provider._send(_Lite(), {"model": "openrouter/x", "messages": []}, [])
    assert sent == []


async def test_the_provider_passes_its_provider_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
    from callobuzz_rag.llm.thinking import ThinkingAttempt

    seen: list[tuple[str | None, str | None]] = []

    async def _record(provider: str | None, model: str | None = None) -> None:
        seen.append((provider, model))

    monkeypatch.setattr(budget, "check_model_call", _record)

    class _Lite:
        async def acompletion(self, **kw: Any) -> str:
            return "raw"

    provider = LiteLLMProvider(Settings(llm_provider="openrouter"))
    attempt = ThinkingAttempt(key="", effective="provider-default")
    await provider._send(_Lite(), {"model": "openrouter/x", "messages": []}, [attempt])
    assert seen == [("openrouter", "openrouter/x")]
