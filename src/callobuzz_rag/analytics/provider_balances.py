"""What the engine's LLM providers have left, for the super admin (billing BIL1).

One reading per provider, each behind the same `BalanceSource`:

- `OpenRouterSource`: the engine key's own limit, what remains, today / week / month and the
  account credits (`analytics/openrouter_balance.py`).
- `OpenAICostsSource` and `AnthropicCostSource`: the provider's **official spend** by day and
  model from their organization admin APIs, with an Admin key (env or entered on the Costs page).
  Neither provider exposes a prepaid balance, so these never say what is left.
- A **manual** balance for any provider (a row the super admin fills in: "loaded $50 on 1 Oct"),
  with an estimate: the balance less what the engine itself recorded as operator-paid spend since
  that date (`usage_daily`). Spend is attributed to a row only when it is the engine's own provider.

Every answer is cached per provider and key fingerprint (five minutes; a failure for one) and
fail-soft: a provider that cannot be reached is `unknown`, never wrong, and the error carries only
the exception class name. Keys are never logged, returned or stored here.

Verified 2026-10-04 against the providers' published API references:

- OpenAI `GET https://api.openai.com/v1/organization/costs` (`start_time`, `end_time` Unix
  seconds, `bucket_width=1d` only, `group_by=line_item`, `limit`, `page`), `Authorization: Bearer
  <Admin key>`. Buckets are `{object: "bucket", start_time, end_time, results: [{object:
  "organization.costs.result", amount: {value: <number>, currency: "usd"}, line_item,
  project_id}]}`; `has_more` / `next_page` paginate. `line_item` names the model ("gpt-4o-mini,
  input"), so the model split comes from the cost call itself and the separate
  `/organization/usage/completions` (tokens per model) call is not needed.
- Anthropic `GET https://api.anthropic.com/v1/organizations/cost_report` (`starting_at`
  RFC 3339; `ending_at` is exclusive and left out; `bucket_width=1d`, `group_by[]=description`,
  `limit` 1..31, `page`),
  headers `x-api-key: <Admin key>` and `anthropic-version: 2023-06-01`. Buckets are `{starting_at,
  ending_at, results: [{amount, currency: "USD", description, model, cost_type, ...}]}` where
  **`amount` is a decimal string in the lowest currency unit (cents)**: `"123.45"` is $1.23.
  `has_more` / `next_page` paginate.
- Neither provider documents a prepaid-balance endpoint.

Money is parsed with `Decimal(str(value))` into USD micro (integers); never float arithmetic
beyond the provider's own JSON number.
"""

import asyncio
import hashlib
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, ClassVar, Final, Literal

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.analytics.openrouter_balance import applies as openrouter_applies
from callobuzz_rag.analytics.openrouter_balance import read_openrouter
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.secrets import EncryptionError, FernetEncryptor, encryptor_from_settings
from callobuzz_rag.db.models.provider_balance_setting import ProviderBalanceSetting
from callobuzz_rag.db.models.usage import UsageDaily

logger = logging.getLogger(__name__)

ProviderId = Literal["openrouter", "openai", "anthropic", "manual"]
Status = Literal["ok", "low", "empty", "unknown", "not_configured"]

CACHE_SECONDS: Final = 300.0
FAILURE_CACHE_SECONDS: Final = 60.0
_TIMEOUT: Final = 8.0
_MAX_PAGES: Final = 10
_MICRO: Final = 1_000_000

OPENAI_COSTS_URL: Final = "https://api.openai.com/v1/organization/costs"
ANTHROPIC_COST_URL: Final = "https://api.anthropic.com/v1/organizations/cost_report"
ANTHROPIC_VERSION: Final = "2023-06-01"

_API_PROVIDERS: Final[tuple[ProviderId, ...]] = ("openrouter", "openai", "anthropic")
PROVIDER_LABELS: Final[dict[str, str]] = {
    "openrouter": "OpenRouter",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
}


@dataclass(frozen=True, slots=True)
class SpendPoint:
    day: date
    model: str | None
    usd_micro: int


@dataclass(frozen=True, slots=True)
class ProviderReading:
    """What one source says; every field may be unknown."""

    remaining_usd_micro: int | None
    limit_usd_micro: int | None
    limit_reset: Literal["daily", "weekly", "monthly"] | None
    resets_at: datetime | None
    spend_today_usd_micro: int | None
    spend_week_usd_micro: int | None
    spend_month_usd_micro: int | None
    official_spend: tuple[SpendPoint, ...] = ()  # by day/model, last 31 days
    account_credits_usd_micro: int | None = None  # OpenRouter /credits (management key)


@dataclass(frozen=True, slots=True)
class ProviderBalance:
    provider: ProviderId
    label: str
    status: Status
    reading: ProviderReading | None
    manual_remaining_usd_micro: int | None  # manual balance - recorded operator spend since then
    manual_since: date | None
    recorded_month_usd_micro: int  # the engine's own ledger (usage_daily, payer=operator)
    low_threshold_usd_micro: int | None
    checked_at: datetime
    error: str | None  # exception class name only, never a message or URL
    # Beyond the pinned fields: what the routes and the panel need.
    id: str | None = None  # the settings row (None: nothing stored yet)
    admin_key_set: bool = False
    admin_key_source: Literal["env", "stored"] | None = None  # the environment wins
    manual_balance_usd_micro: int | None = None
    manual_balance_at: datetime | None = None
    manual_note: str | None = None
    unpriced_requests: int = 0  # operator requests with an unknown price in the estimate window
    spend_tracked: bool = True  # False: the engine's own spend is not attributed to this row
    alert_state: str = "ok"
    remaining_usd_micro: int | None = field(default=None)  # the figure the status is judged on


def usd_to_micro(value: object) -> int | None:
    """A provider's USD number (int, float or decimal string) as USD micro, exactly."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    return int((d * _MICRO).to_integral_value(ROUND_HALF_UP))


def _cents_to_micro(value: object) -> int | None:
    """Anthropic's `amount`: a decimal string in cents. 1 cent = 10_000 USD micro."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return None
    if not d.is_finite():
        return None
    return int((d * 10_000).to_integral_value(ROUND_HALF_UP))


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _windows(now: datetime) -> tuple[date, date, date]:
    """(today, Monday of this week, first of this month), UTC."""
    today = now.astimezone(UTC).date()
    return today, today - timedelta(days=today.weekday()), today.replace(day=1)


def _reading_from_points(
    points: dict[tuple[date, str | None], int], now: datetime, *, keep_from: date
) -> ProviderReading:
    today, week, month = _windows(now)
    kept = {k: v for k, v in points.items() if k[0] >= keep_from}
    spend_today = sum(v for (d, _), v in kept.items() if d == today)
    spend_week = sum(v for (d, _), v in kept.items() if d >= week)
    spend_month = sum(v for (d, _), v in kept.items() if d >= month)
    ordered = tuple(
        SpendPoint(d, m, v)
        for (d, m), v in sorted(kept.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))
    )
    return ProviderReading(
        remaining_usd_micro=None,
        limit_usd_micro=None,
        limit_reset=None,
        resets_at=None,
        spend_today_usd_micro=spend_today,
        spend_week_usd_micro=spend_week,
        spend_month_usd_micro=spend_month,
        official_spend=ordered,
    )


class BalanceSource(ABC):
    provider: ClassVar[ProviderId]

    @abstractmethod
    def configured(self, settings: Settings, row: ProviderBalanceSetting | None) -> bool: ...

    @abstractmethod
    async def read(
        self, client: httpx.AsyncClient, *, key: str, now: datetime
    ) -> ProviderReading: ...


class OpenRouterSource(BalanceSource):
    provider: ClassVar[ProviderId] = "openrouter"

    def __init__(self, settings: Settings | None = None, management_key: str = "") -> None:
        self._threshold = settings.openrouter_low_balance_usd if settings is not None else 0.0
        self._management_key = management_key

    def configured(self, settings: Settings, row: ProviderBalanceSetting | None) -> bool:
        return openrouter_applies(settings)

    async def read(self, client: httpx.AsyncClient, *, key: str, now: datetime) -> ProviderReading:
        b = await read_openrouter(
            client,
            engine_key=key,
            management_key=self._management_key,
            threshold=self._threshold,
            now=now,
        )
        reset = b.limit_reset
        return ProviderReading(
            remaining_usd_micro=usd_to_micro(b.remaining_usd),
            limit_usd_micro=usd_to_micro(b.limit_usd),
            limit_reset=reset if reset in ("daily", "weekly", "monthly") else None,  # type: ignore[arg-type]
            resets_at=b.resets_at,
            spend_today_usd_micro=usd_to_micro(b.usage_daily_usd),
            spend_week_usd_micro=usd_to_micro(b.usage_weekly_usd),
            spend_month_usd_micro=usd_to_micro(b.usage_monthly_usd),
            account_credits_usd_micro=usd_to_micro(b.account_credits_usd),
        )


class OpenAICostsSource(BalanceSource):
    provider: ClassVar[ProviderId] = "openai"

    def configured(self, settings: Settings, row: ProviderBalanceSetting | None) -> bool:
        return bool(settings.openai_admin_key.get_secret_value()) or bool(
            row is not None and row.admin_key_encrypted
        )

    async def read(self, client: httpx.AsyncClient, *, key: str, now: datetime) -> ProviderReading:
        today, _, _ = _windows(now)
        first = today - timedelta(days=30)
        params: dict[str, Any] = {
            "start_time": int(_midnight(first).timestamp()),
            "end_time": int(now.timestamp()),
            "bucket_width": "1d",
            "group_by": "line_item",
            "limit": 31,
        }
        points: dict[tuple[date, str | None], int] = {}
        for _ in range(_MAX_PAGES):
            res = await client.get(
                OPENAI_COSTS_URL, params=params, headers={"Authorization": f"Bearer {key}"}
            )
            res.raise_for_status()
            body = res.json() or {}
            for bucket in body.get("data") or []:
                start = bucket.get("start_time")
                if not isinstance(start, int | float):
                    continue
                day = datetime.fromtimestamp(start, UTC).date()
                for item in bucket.get("results") or []:
                    amount = item.get("amount") or {}
                    if str(amount.get("currency", "usd")).lower() != "usd":
                        continue
                    micro = usd_to_micro(amount.get("value"))
                    if micro is None:
                        continue
                    line = item.get("line_item")
                    k = (day, line if isinstance(line, str) and line else None)
                    points[k] = points.get(k, 0) + micro
            nxt = body.get("next_page")
            if not body.get("has_more") or not nxt:
                break
            params = {**params, "page": nxt}
        return _reading_from_points(points, now, keep_from=first)


class AnthropicCostSource(BalanceSource):
    provider: ClassVar[ProviderId] = "anthropic"

    def configured(self, settings: Settings, row: ProviderBalanceSetting | None) -> bool:
        return bool(settings.anthropic_admin_key.get_secret_value()) or bool(
            row is not None and row.admin_key_encrypted
        )

    async def read(self, client: httpx.AsyncClient, *, key: str, now: datetime) -> ProviderReading:
        today, _, _ = _windows(now)
        first = today - timedelta(days=30)
        params: dict[str, Any] = {
            "starting_at": _midnight(first).strftime("%Y-%m-%dT%H:%M:%SZ"),
            # No `ending_at`: it is exclusive (buckets that end before it), so tomorrow's midnight
            # would be read strictly and today's bucket dropped; left out, the API runs up to now.
            "bucket_width": "1d",
            "group_by[]": "description",
            "limit": 31,
        }
        headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
        points: dict[tuple[date, str | None], int] = {}
        for _ in range(_MAX_PAGES):
            res = await client.get(ANTHROPIC_COST_URL, params=params, headers=headers)
            res.raise_for_status()
            body = res.json() or {}
            for bucket in body.get("data") or []:
                start = bucket.get("starting_at")
                if not isinstance(start, str):
                    continue
                day = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(UTC).date()
                for item in bucket.get("results") or []:
                    if str(item.get("currency", "USD")).upper() != "USD":
                        continue
                    micro = _cents_to_micro(item.get("amount"))
                    if micro is None:
                        continue
                    name = item.get("model") or item.get("description")
                    k = (day, name if isinstance(name, str) and name else None)
                    points[k] = points.get(k, 0) + micro
            nxt = body.get("next_page")
            if not body.get("has_more") or not nxt:
                break
            params = {**params, "page": nxt}
        return _reading_from_points(points, now, keep_from=first)


# --- cache: per provider and key fingerprint -----------------------------------------------

# key -> (stored_at, ttl, reading | None, error class name | None)
_cache: dict[str, tuple[float, float, ProviderReading | None, str | None]] = {}
_locks: dict[str, asyncio.Lock] = {}  # one per cache key: a slow provider holds up only itself


def reset_cache() -> None:
    """Forget every cached reading (tests, and after a key or setting change)."""
    _cache.clear()
    _locks.clear()


def forget(provider: str) -> None:
    for k in [k for k in _cache if k.startswith(f"{provider}:")]:
        del _cache[k]


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


async def read_cached(
    source: BalanceSource,
    client: httpx.AsyncClient,
    *,
    key: str,
    now: datetime,
    cache_key: str | None = None,
    refresh: bool = False,
    timeout: float | None = None,
) -> tuple[ProviderReading | None, str | None]:
    """(reading, error): a failure is cached for a minute and surfaces as the class name. A
    `timeout` (seconds, for the pages that must not wait) turns a slow provider into a cached
    `TimeoutError`, so the next poll is answered at once."""
    ck = cache_key or f"{source.provider}:{_fingerprint(key)}"
    clock = time.monotonic()
    hit = _cache.get(ck)
    if hit is not None and not refresh and clock - hit[0] < hit[1]:
        return hit[2], hit[3]
    async with _locks.setdefault(ck, asyncio.Lock()):
        hit = _cache.get(ck)
        if hit is not None and not refresh and time.monotonic() - hit[0] < hit[1]:
            return hit[2], hit[3]
        try:
            reading = await asyncio.wait_for(source.read(client, key=key, now=now), timeout)
        except Exception as exc:
            # The text can carry the request URL but never the key; log the class only.
            name = type(exc).__name__
            logger.warning("%s balance unavailable (%s)", source.provider, name)
            _cache[ck] = (time.monotonic(), FAILURE_CACHE_SECONDS, None, name)
            return None, name
        _cache[ck] = (time.monotonic(), CACHE_SECONDS, reading, None)
        return reading, None


async def ensure_row(
    session: AsyncSession, provider: str, label: str, *, lock: bool = False
) -> tuple[ProviderBalanceSetting, bool]:
    """The one stored row of an API provider, created when missing: (row, created).

    A plain INSERT would race a second writer (the beat run and a first PUT, or two beat
    runs) into the unique index and fail; `ON CONFLICT DO NOTHING` then a SELECT cannot.
    `lock` takes the row `FOR UPDATE` (and refreshes it) so only one writer at a time moves
    its alert state."""
    inserted = await session.execute(
        pg_insert(ProviderBalanceSetting)
        .values(provider=provider, label=label, alert_state="ok")
        .on_conflict_do_nothing(
            index_elements=["provider"], index_where=text("provider <> 'manual'")
        )
    )
    stmt = select(ProviderBalanceSetting).where(ProviderBalanceSetting.provider == provider)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = (await session.execute(stmt)).scalar_one()
    return row, bool(inserted.rowcount)  # type: ignore[attr-defined]


# --- the manual estimate -------------------------------------------------------------------


async def operator_spend_since(session: AsyncSession, since: date) -> tuple[int, int]:
    """(USD micro, unpriced requests) the engine recorded as operator-paid from `since` (UTC
    day, inclusive) on: what its own key spent."""
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(UsageDaily.cost_micro), 0),
                func.coalesce(func.sum(UsageDaily.unpriced_requests), 0),
            ).where(UsageDaily.payer == "operator", UsageDaily.day >= since)
        )
    ).one()
    return int(row[0]), int(row[1])


def tracks_spend(row: ProviderBalanceSetting, engine_provider: str) -> bool:
    """Whether the engine's recorded operator spend belongs to this row: only the provider the
    engine itself calls (`RAG_LLM_PROVIDER`), by id or, for a manual row, by its label."""
    engine = (engine_provider or "").strip().lower()
    if not engine:
        return False
    if row.provider == "manual":
        return row.label.strip().lower() == engine
    return row.provider == engine


@dataclass(frozen=True, slots=True)
class ManualEstimate:
    manual_remaining_usd_micro: int | None
    manual_since: date | None
    unpriced_requests: int
    spend_tracked: bool


async def manual_estimate(
    session: AsyncSession, row: ProviderBalanceSetting, engine_provider: str, now: datetime
) -> ManualEstimate:
    """Entered balance less the engine's operator spend from that day on. The day itself counts
    (spend earlier that day lowers the estimate: conservative)."""
    if row.manual_balance_usd_micro is None or row.manual_balance_at is None:
        return ManualEstimate(None, None, 0, tracks_spend(row, engine_provider))
    since = row.manual_balance_at.astimezone(UTC).date()
    if not tracks_spend(row, engine_provider):
        return ManualEstimate(row.manual_balance_usd_micro, since, 0, False)
    spent, unpriced = await operator_spend_since(session, since)
    return ManualEstimate(row.manual_balance_usd_micro - spent, since, unpriced, True)


# --- status --------------------------------------------------------------------------------


def judge(remaining: int | None, threshold: int | None) -> Status:
    if remaining is None:
        return "unknown"
    if remaining <= 0:
        return "empty"
    if threshold is not None and remaining < threshold:
        return "low"
    return "ok"


_SEVERITY: Final[dict[str, int]] = {"empty": 3, "low": 2, "unknown": 1, "ok": 0}


def worst(balances: Sequence[ProviderBalance]) -> ProviderBalance | None:
    """The provider to show in the header: empty > low > unknown > ok; not_configured ignored."""
    candidates = [b for b in balances if b.status != "not_configured"]
    if not candidates:
        return None
    return max(candidates, key=lambda b: _SEVERITY[b.status])


# --- the aggregator ------------------------------------------------------------------------


def _env_key(provider: str, settings: Settings) -> str:
    secret = {
        "openrouter": settings.openrouter_management_key,
        "openai": settings.openai_admin_key,
        "anthropic": settings.anthropic_admin_key,
    }.get(provider)
    return secret.get_secret_value() if secret is not None else ""


def resolve_key(
    provider: str,
    settings: Settings,
    row: ProviderBalanceSetting | None,
    encryptor: FernetEncryptor | None,
) -> str:
    """The admin key: the environment wins, else the row's decrypted one, else empty."""
    env = _env_key(provider, settings)
    if env:
        return env
    if row is not None and row.admin_key_encrypted and encryptor is not None:
        try:
            return encryptor.decrypt(row.admin_key_encrypted)
        except EncryptionError:
            logger.warning("%s admin key could not be decrypted", provider)
    return ""


def _source_for(provider: ProviderId, settings: Settings, management_key: str) -> BalanceSource:
    if provider == "openrouter":
        return OpenRouterSource(settings, management_key)
    if provider == "openai":
        return OpenAICostsSource()
    return AnthropicCostSource()


@dataclass(frozen=True, slots=True)
class _Fetched:
    reading: ProviderReading | None = None
    error: str | None = None
    source_configured: bool = False


async def _fetch(
    provider: ProviderId,
    row: ProviderBalanceSetting | None,
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    encryptor: FernetEncryptor | None,
    now: datetime,
    refresh: bool,
    timeout: float | None,
) -> _Fetched:
    # The HTTP half of a provider balance: no database, so the providers run concurrently.
    reading: ProviderReading | None = None
    error: str | None = None
    source_configured = False
    key = resolve_key(provider, settings, row, encryptor) if provider != "manual" else ""
    if provider != "manual":
        source = _source_for(provider, settings, key)
        source_configured = source.configured(settings, row) or bool(key)
        if provider == "openrouter":
            source_configured = openrouter_applies(settings)
            call_key = settings.llm_api_key.get_secret_value()
            cache_key = f"openrouter:{_fingerprint(call_key + '|' + key)}"
        else:
            call_key = key
            cache_key = None
        if source_configured and call_key:
            reading, error = await read_cached(
                source,
                client,
                key=call_key,
                now=now,
                cache_key=cache_key,
                refresh=refresh,
                timeout=timeout,
            )
    return _Fetched(reading, error, source_configured)


async def _balance_for(
    provider: ProviderId,
    row: ProviderBalanceSetting | None,
    *,
    settings: Settings,
    session: AsyncSession,
    fetched: _Fetched,
    engine_provider: str,
    month_spend: tuple[int, int],
    now: datetime,
) -> ProviderBalance:
    label = row.label if row is not None else PROVIDER_LABELS.get(provider, provider)
    reading, error, source_configured = fetched.reading, fetched.error, fetched.source_configured
    estimate = ManualEstimate(None, None, 0, row is not None and tracks_spend(row, engine_provider))
    if row is not None and row.manual_balance_usd_micro is not None:
        estimate = await manual_estimate(session, row, engine_provider, now)

    threshold = row.low_threshold_usd_micro if row is not None else None
    if threshold is None and provider == "openrouter":
        threshold = usd_to_micro(settings.openrouter_low_balance_usd)

    has_manual = estimate.manual_remaining_usd_micro is not None
    if not source_configured and not has_manual:
        status: Status = "not_configured"
        remaining: int | None = None
    else:
        remaining = (
            reading.remaining_usd_micro
            if reading is not None and reading.remaining_usd_micro is not None
            else estimate.manual_remaining_usd_micro
        )
        status = judge(remaining, threshold)

    recorded = month_spend[0] if row is not None and tracks_spend(row, engine_provider) else 0
    if row is None and provider == engine_provider:
        recorded = month_spend[0]
    return ProviderBalance(
        provider=provider,
        label=label,
        status=status,
        reading=reading,
        manual_remaining_usd_micro=estimate.manual_remaining_usd_micro,
        manual_since=estimate.manual_since,
        recorded_month_usd_micro=recorded,
        low_threshold_usd_micro=threshold,
        checked_at=now,
        error=error,
        id=str(row.id) if row is not None else None,
        admin_key_set=bool(_env_key(provider, settings) or (row and row.admin_key_encrypted)),
        admin_key_source=(
            "env"
            if _env_key(provider, settings)
            else "stored"
            if row is not None and row.admin_key_encrypted
            else None
        ),
        manual_balance_usd_micro=row.manual_balance_usd_micro if row is not None else None,
        manual_balance_at=row.manual_balance_at if row is not None else None,
        manual_note=row.manual_note if row is not None else None,
        unpriced_requests=estimate.unpriced_requests,
        spend_tracked=estimate.spend_tracked if row is not None else provider == engine_provider,
        alert_state=row.alert_state if row is not None else "ok",
        remaining_usd_micro=remaining,
    )


async def provider_balances(
    settings: Settings,
    session: AsyncSession,
    *,
    refresh: bool = False,
    only: str | None = None,
    timeout: float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    now: datetime | None = None,
) -> list[ProviderBalance]:
    """OpenRouter, OpenAI and Anthropic (each `not_configured` until it has a key or a manual
    balance) and then every manual row. `only` is a provider id or a manual row's UUID. The
    providers are asked concurrently; a `timeout` bounds each one (the header and the Costs
    page pass one, the panel and the beat do not)."""
    now = now or datetime.now(UTC)
    rows = list((await session.execute(select(ProviderBalanceSetting))).scalars().all())
    by_provider = {r.provider: r for r in rows if r.provider != "manual"}
    manual_rows = sorted((r for r in rows if r.provider == "manual"), key=lambda r: r.label.lower())
    encryptor = encryptor_from_settings(settings)
    engine_provider = (settings.llm_provider or "").lower()
    month_spend = await operator_spend_since(session, now.astimezone(UTC).date().replace(day=1))

    targets: list[tuple[ProviderId, ProviderBalanceSetting | None]] = [
        (p, by_provider.get(p)) for p in _API_PROVIDERS
    ] + [("manual", r) for r in manual_rows]
    if only is not None:
        targets = [
            t for t in targets if t[0] == only or (t[1] is not None and str(t[1].id) == only)
        ]

    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        fetched = await asyncio.gather(
            *(
                _fetch(
                    provider,
                    row,
                    settings=settings,
                    client=client,
                    encryptor=encryptor,
                    now=now,
                    refresh=refresh,
                    timeout=timeout,
                )
                for provider, row in targets
            )
        )
    # The database half stays sequential: one session is never used by two tasks at once.
    out: list[ProviderBalance] = []
    for (provider, row), got in zip(targets, fetched, strict=True):
        out.append(
            await _balance_for(
                provider,
                row,
                settings=settings,
                session=session,
                fetched=got,
                engine_provider=engine_provider,
                month_spend=month_spend,
                now=now,
            )
        )
    return out
