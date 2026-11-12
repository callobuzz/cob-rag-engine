"""The engine's OpenRouter key balance, for the dashboard header and the Costs page.

Only when the engine's own LLM key is an OpenRouter key (`RAG_LLM_PROVIDER=openrouter`). It asks
OpenRouter's `GET /api/v1/key` (the key's own spend limit, what remains of it, and what it has
spent) and, when the key has no spend limit, `GET /api/v1/credits` (the account's purchased
credits less its usage; OpenRouter may refuse that one to a non-management key, which is fine:
the balance is then simply unknown).

The answer is cached for five minutes in this process so a header polling every minute does not
become a request to OpenRouter every minute, and a failure is cached for a minute for the same
reason. The key is read from settings and sent only to openrouter.ai; it is never logged,
returned or stored here.

Verified 2026-10-04 against OpenRouter's published docs (docs repo `openrouterteam/docs`, the
OpenAPI spec and the `/api/v1/key` reference page):
- `GET /api/v1/key` (Bearer = the calling key) -> `data: {label, limit, limit_remaining,
  limit_reset ("daily" | "weekly" | "monthly" | null), usage, usage_daily, usage_weekly,
  usage_monthly, byok_usage*, is_free_tier, ...}`; USD numbers; `limit*` null = no limit.
- Reset times: "Resets happen automatically at midnight UTC, and weeks are Monday through
  Sunday" (the key-management docs). Monthly is taken as the first of the month, 00:00 UTC (the
  docs give no other boundary); an unknown `limit_reset` word gives no `resets_at`.
- `GET /api/v1/credits` -> `data: {total_credits, total_usage}`; a **management key** is
  required (401 without auth, 403 for any other key), so a refusal means "unknown", not an error.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

OPENROUTER_API = "https://openrouter.ai/api/v1"
CACHE_SECONDS = 300.0
FAILURE_CACHE_SECONDS = 60.0
_TIMEOUT = 8.0


@dataclass(frozen=True, slots=True)
class OpenRouterBalance:
    """`remaining_usd` is None when OpenRouter did not say (no spend limit and no credits
    access): the card then shows usage only and never warns."""

    remaining_usd: float | None
    limit_usd: float | None
    usage_usd: float | None
    usage_monthly_usd: float | None
    low: bool
    available: bool = True
    checked_at: float = 0.0
    # BIL1: the rest of what /key and /credits say.
    limit_reset: str | None = None  # "daily" | "weekly" | "monthly" | None
    resets_at: datetime | None = None
    usage_daily_usd: float | None = None
    usage_weekly_usd: float | None = None
    account_credits_usd: float | None = None  # total_credits - total_usage (management key)
    label: str | None = None


_cache: tuple[float, float, OpenRouterBalance] | None = None  # (stored_at, ttl, value)
_lock = asyncio.Lock()


def reset_cache() -> None:
    """Forget the cached balance (tests, and a key change)."""
    global _cache
    _cache = None


def applies(settings: Settings) -> bool:
    """Whether the engine's own LLM key is an OpenRouter key."""
    return (
        (settings.llm_provider or "").lower() == "openrouter"
        and bool(settings.llm_api_key.get_secret_value())
        and not settings.llm_api_base
    )


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


_RESET_WORDS = ("daily", "weekly", "monthly")


def next_reset(limit_reset: str | None, now: datetime) -> datetime | None:
    """When a key's limit next resets, in UTC: midnight for daily, Monday midnight for weekly
    (weeks run Monday to Sunday), the first of the month for monthly. None for anything else:
    a time is never invented."""
    now = now.astimezone(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if limit_reset == "daily":
        return midnight + timedelta(days=1)
    if limit_reset == "weekly":
        return midnight + timedelta(days=7 - now.weekday())
    if limit_reset == "monthly":
        year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
        return datetime(year, month, 1, tzinfo=UTC)
    return None


def _parse(
    key_data: dict[str, Any],
    credits_data: dict[str, Any] | None,
    threshold: float,
    *,
    now: datetime | None = None,
) -> OpenRouterBalance:
    limit = _number(key_data.get("limit"))
    remaining = _number(key_data.get("limit_remaining"))
    usage = _number(key_data.get("usage"))
    account_credits: float | None = None
    if credits_data is not None:
        total = _number(credits_data.get("total_credits"))
        used = _number(credits_data.get("total_usage"))
        if total is not None and used is not None:
            account_credits = float(Decimal(str(total)) - Decimal(str(used)))
            # The tighter of the key's own limit and what the account can still pay: a key
            # with room left on an account that is nearly empty fails at the account's figure.
            remaining = account_credits if remaining is None else min(remaining, account_credits)
    reset = key_data.get("limit_reset")
    limit_reset = reset if isinstance(reset, str) and reset in _RESET_WORDS else None
    label = key_data.get("label")
    return OpenRouterBalance(
        remaining_usd=remaining,
        limit_usd=limit,
        usage_usd=usage,
        usage_monthly_usd=_number(key_data.get("usage_monthly")),
        low=remaining is not None and remaining < threshold,
        checked_at=time.time(),
        limit_reset=limit_reset,
        resets_at=next_reset(limit_reset, now or datetime.now(UTC)),
        usage_daily_usd=_number(key_data.get("usage_daily")),
        usage_weekly_usd=_number(key_data.get("usage_weekly")),
        account_credits_usd=account_credits,
        label=label if isinstance(label, str) else None,
    )


async def fetch_openrouter(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> OpenRouterBalance:
    """Ask OpenRouter: `/key` with the engine key, `/credits` with the management key when one
    is set, else with the engine key when `/key` gave no remaining figure. A refusal of
    `/credits` is not an error: the account credits are just unknown."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        return await read_openrouter(
            client,
            engine_key=settings.llm_api_key.get_secret_value(),
            management_key=settings.openrouter_management_key.get_secret_value(),
            threshold=settings.openrouter_low_balance_usd,
        )


async def read_openrouter(
    client: httpx.AsyncClient,
    *,
    engine_key: str,
    management_key: str,
    threshold: float,
    now: datetime | None = None,
) -> OpenRouterBalance:
    """`fetch_openrouter` on a caller-supplied client (the provider balances share one)."""
    res = await client.get(
        f"{OPENROUTER_API}/key", headers={"Authorization": f"Bearer {engine_key}"}
    )
    res.raise_for_status()
    key_data: dict[str, Any] = (res.json() or {}).get("data") or {}
    credits_data: dict[str, Any] | None = None
    if management_key or _number(key_data.get("limit_remaining")) is None:
        try:
            credits = await client.get(
                f"{OPENROUTER_API}/credits",
                headers={"Authorization": f"Bearer {management_key or engine_key}"},
            )
            if credits.status_code == 200:
                credits_data = (credits.json() or {}).get("data") or {}
        except httpx.HTTPError:
            credits_data = None
    return _parse(key_data, credits_data, threshold, now=now)


async def _fetch(settings: Settings) -> OpenRouterBalance:
    return await fetch_openrouter(settings)


async def get_openrouter_balance(settings: Settings) -> OpenRouterBalance | None:
    """The cached balance, refreshed at most every five minutes. None when the engine key is
    not an OpenRouter key; an `available=False` value when OpenRouter could not be asked."""
    global _cache
    if not applies(settings):
        return None
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _cache[1]:
        return _cache[2]
    async with _lock:
        now = time.monotonic()
        if _cache is not None and now - _cache[0] < _cache[1]:
            return _cache[2]
        try:
            value = await _fetch(settings)
            _cache = (now, CACHE_SECONDS, value)
        except Exception as exc:
            # The exception text can carry the request URL but never the key; log the class only.
            logger.warning("OpenRouter balance unavailable (%s)", type(exc).__name__)
            value = OpenRouterBalance(
                None, None, None, None, low=False, available=False, checked_at=time.time()
            )
            _cache = (now, FAILURE_CACHE_SECONDS, value)
        return value
