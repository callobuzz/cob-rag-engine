"""BIL1: OpenRouter in full (reset period, daily and weekly spend, account credits)."""

from datetime import UTC, datetime

import httpx
import pytest

from callobuzz_rag.analytics import openrouter_balance as orb
from callobuzz_rag.analytics.openrouter_balance import _parse, fetch_openrouter, next_reset
from callobuzz_rag.core.config import Settings

NOW = datetime(2026, 10, 4, 10, tzinfo=UTC)  # a Sunday
KEY = {
    "data": {
        "label": "engine",
        "limit": 50.0,
        "limit_remaining": 12.5,
        "limit_reset": "monthly",
        "usage": 37.5,
        "usage_daily": 1.25,
        "usage_weekly": 6.0,
        "usage_monthly": 37.5,
        "is_free_tier": False,
    }
}
KEY_NO_LIMIT = {
    "data": {**KEY["data"], "limit": None, "limit_remaining": None, "limit_reset": None}
}
CREDITS = {"data": {"total_credits": 20.0, "total_usage": 18.5}}


def make_settings(**kw: str) -> Settings:
    base = {"llm_provider": "openrouter", "llm_api_key": "sk-or-engine-AAAA"}
    return Settings(**{**base, **kw})  # type: ignore[arg-type]


def test_parse_reads_every_field() -> None:
    b = _parse(KEY["data"], None, threshold=5.0, now=NOW)
    assert (b.limit_usd, b.remaining_usd, b.limit_reset) == (50.0, 12.5, "monthly")
    assert (b.usage_daily_usd, b.usage_weekly_usd, b.usage_monthly_usd) == (1.25, 6.0, 37.5)
    assert b.resets_at == datetime(2026, 11, 1, tzinfo=UTC)
    assert b.label == "engine"
    assert b.low is False


def test_no_limit_falls_back_to_credits_and_unknown_stays_unknown() -> None:
    data = KEY_NO_LIMIT["data"]
    b = _parse(data, CREDITS["data"], 5.0, now=NOW)
    assert b.remaining_usd == 1.5 and b.account_credits_usd == 1.5 and b.low is True
    unknown = _parse(data, None, 5.0, now=NOW)
    assert unknown.remaining_usd is None and unknown.low is False and unknown.resets_at is None


def test_reset_boundaries() -> None:
    assert next_reset("daily", NOW) == datetime(2026, 10, 5, tzinfo=UTC)
    assert next_reset("weekly", NOW) == datetime(2026, 10, 5, tzinfo=UTC)  # Sunday -> Monday
    assert next_reset("weekly", datetime(2026, 10, 5, 0, 0, tzinfo=UTC)) == datetime(
        2026, 10, 12, tzinfo=UTC
    )
    assert next_reset("monthly", datetime(2026, 12, 31, 23, tzinfo=UTC)) == datetime(
        2027, 1, 1, tzinfo=UTC
    )
    assert next_reset("yearly", NOW) is None and next_reset(None, NOW) is None


async def test_credits_uses_the_management_key_when_set() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(f"{req.url.path} {req.headers['authorization'][-4:]}")
        return httpx.Response(200, json=KEY_NO_LIMIT if req.url.path.endswith("/key") else CREDITS)

    settings = make_settings(openrouter_management_key="sk-or-mgmt-BBBB")
    b = await fetch_openrouter(settings, transport=httpx.MockTransport(handler))
    assert seen == ["/api/v1/key AAAA", "/api/v1/credits BBBB"]
    assert b.account_credits_usd == 1.5


async def test_credits_with_engine_key_only_when_key_has_no_remaining() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        return httpx.Response(200, json=KEY)

    await fetch_openrouter(make_settings(), transport=httpx.MockTransport(handler))
    assert seen == ["/api/v1/key"]


async def test_credits_refusal_is_unknown_not_an_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/key"):
            return httpx.Response(200, json=KEY_NO_LIMIT)
        return httpx.Response(403, json={"error": {"code": 403}})

    b = await fetch_openrouter(make_settings(), transport=httpx.MockTransport(handler))
    assert b.remaining_usd is None and b.account_credits_usd is None


async def test_failure_is_cached_and_logged_without_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = 0

    async def boom(settings: Settings) -> orb.OpenRouterBalance:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("https://openrouter.ai/?key=sk-or-engine-AAAA")

    monkeypatch.setattr(orb, "_fetch", boom)
    orb.reset_cache()
    settings = make_settings()
    first = await orb.get_openrouter_balance(settings)
    second = await orb.get_openrouter_balance(settings)
    assert first is not None and first.available is False and second is first and calls == 1
    assert "ConnectError" in caplog.text and "sk-or-engine" not in caplog.text
    orb.reset_cache()


def test_remaining_is_the_smaller_of_key_limit_and_account_credits() -> None:
    data = {**KEY["data"], "limit_remaining": 48.0}
    credits = {"total_credits": 20, "total_usage": 18}
    b = _parse(data, credits, 5.0, now=NOW)
    assert b.remaining_usd == 2.0 and b.account_credits_usd == 2.0 and b.low is True
    # The key's own limit is the tighter one: it wins.
    tight = _parse({**KEY["data"], "limit_remaining": 1.0}, credits, 5.0, now=NOW)
    assert tight.remaining_usd == 1.0


def test_account_credits_subtraction_is_exact() -> None:
    b = _parse(KEY_NO_LIMIT["data"], {"total_credits": 0.3, "total_usage": 0.1}, 0.0, now=NOW)
    assert b.remaining_usd == 0.2
