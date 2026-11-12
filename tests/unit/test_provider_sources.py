"""BIL1: OpenAI and Anthropic spend, the manual estimate, the aggregator. No real HTTP."""

from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest

from callobuzz_rag.analytics import provider_balances as pb
from callobuzz_rag.analytics.provider_balances import (
    AnthropicCostSource,
    OpenAICostsSource,
    ProviderBalance,
    SpendPoint,
    judge,
    manual_estimate,
    provider_balances,
    worst,
)
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.provider_balance_setting import ProviderBalanceSetting

NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)


def _ts(day: int) -> int:
    return int(datetime(2026, 10, day, tzinfo=UTC).timestamp())


# Shaped like the OpenAI Costs API reference: buckets of organization.costs.result.
OPENAI_PAGE_1 = {
    "object": "page",
    "data": [
        {
            "object": "bucket",
            "start_time": _ts(1),
            "end_time": _ts(2),
            "results": [
                {
                    "object": "organization.costs.result",
                    "amount": {"value": 1.23, "currency": "usd"},
                    "line_item": "gpt-4o-mini",
                    "project_id": None,
                }
            ],
        }
    ],
    "has_more": True,
    "next_page": "page_2",
}
OPENAI_PAGE_2 = {
    "object": "page",
    "data": [
        {
            "object": "bucket",
            "start_time": _ts(4),
            "end_time": _ts(5),
            "results": [
                {
                    "object": "organization.costs.result",
                    "amount": {"value": 11.11, "currency": "usd"},
                    "line_item": "gpt-4o",
                },
                {"amount": {"value": 5.0, "currency": "eur"}, "line_item": "ignored"},
            ],
        }
    ],
    "has_more": False,
    "next_page": None,
}
# Shaped like the Anthropic cost report: amount is a decimal string in CENTS.
ANTHROPIC_PAGE = {
    "data": [
        {
            "starting_at": "2026-10-03T00:00:00Z",
            "ending_at": "2026-10-04T00:00:00Z",
            "results": [
                {
                    "amount": "123.45",
                    "currency": "USD",
                    "description": "Claude Haiku Usage - Input Tokens",
                    "model": "claude-haiku-4-5",
                    "cost_type": "tokens",
                },
                {
                    "amount": "50",
                    "currency": "USD",
                    "description": "Web search",
                    "model": None,
                    "cost_type": "web_search",
                },
            ],
        }
    ],
    "has_more": False,
    "next_page": None,
}


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_openai_costs_sum_by_day_and_line_item_across_pages() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(
            200, json=OPENAI_PAGE_2 if "page" in req.url.params else OPENAI_PAGE_1
        )

    async with client_for(handler) as client:
        reading = await OpenAICostsSource().read(client, key="sk-admin-x", now=NOW)
    assert seen[0].headers["authorization"] == "Bearer sk-admin-x"
    assert (
        seen[0].url.params["bucket_width"] == "1d" and seen[0].url.params["group_by"] == "line_item"
    )
    assert seen[1].url.params["page"] == "page_2"
    assert reading.spend_month_usd_micro == 12_340_000  # 12.34 USD, Decimal-exact
    assert reading.spend_today_usd_micro == 11_110_000
    assert reading.official_spend[0] == SpendPoint(date(2026, 10, 1), "gpt-4o-mini", 1_230_000)
    assert reading.remaining_usd_micro is None  # no balance API


async def test_anthropic_cost_report_units_and_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=ANTHROPIC_PAGE)

    async with client_for(handler) as client:
        reading = await AnthropicCostSource().read(client, key="sk-ant-admin-x", now=NOW)
    req = seen[0]
    assert req.headers["x-api-key"] == "sk-ant-admin-x" and req.headers["anthropic-version"]
    assert req.url.params["group_by[]"] == "description"
    # 123.45 cents = $1.2345 and 50 cents = $0.50, in USD micro.
    assert reading.spend_month_usd_micro == 1_234_500 + 500_000
    names = {p.model: p.usd_micro for p in reading.official_spend}
    assert names == {"claude-haiku-4-5": 1_234_500, "Web search": 500_000}
    assert reading.spend_today_usd_micro == 0  # the buckets are from the 3rd


def setting(**kw: Any) -> ProviderBalanceSetting:
    base: dict[str, Any] = {"provider": "manual", "label": "Groq", "alert_state": "ok"}
    return ProviderBalanceSetting(**{**base, **kw})


async def test_manual_estimate_subtracts_operator_spend_since_its_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[date] = []

    async def spend(session: Any, since: date) -> tuple[int, int]:
        asked.append(since)
        return 3_000_000, 4

    monkeypatch.setattr(pb, "operator_spend_since", spend)
    row = setting(
        manual_balance_usd_micro=50_000_000, manual_balance_at=datetime(2026, 10, 1, 9, tzinfo=UTC)
    )
    est = await manual_estimate(object(), row, engine_provider="groq", now=NOW)  # type: ignore[arg-type]
    assert asked == [date(2026, 10, 1)]  # inclusive of the day itself
    assert est.manual_remaining_usd_micro == 47_000_000
    assert est.unpriced_requests == 4 and est.spend_tracked is True


async def test_manual_estimate_without_spend_tracking_for_another_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def spend(session: Any, since: date) -> tuple[int, int]:
        raise AssertionError("must not be asked")

    monkeypatch.setattr(pb, "operator_spend_since", spend)
    row = setting(
        manual_balance_usd_micro=50_000_000, manual_balance_at=datetime(2026, 10, 1, tzinfo=UTC)
    )
    est = await manual_estimate(object(), row, engine_provider="openrouter", now=NOW)  # type: ignore[arg-type]
    assert est.manual_remaining_usd_micro == 50_000_000 and est.spend_tracked is False


def bal(status: str) -> ProviderBalance:
    return ProviderBalance(
        provider="openai",
        label="x",
        status=status,  # type: ignore[arg-type]
        reading=None,
        manual_remaining_usd_micro=None,
        manual_since=None,
        recorded_month_usd_micro=0,
        low_threshold_usd_micro=None,
        checked_at=NOW,
        error=None,
    )


def test_worst_orders_empty_low_unknown_ok_and_ignores_not_configured() -> None:
    assert worst([bal("ok"), bal("unknown"), bal("not_configured")]).status == "unknown"  # type: ignore[union-attr]
    assert worst([bal("low"), bal("empty")]).status == "empty"  # type: ignore[union-attr]
    assert worst([bal("not_configured")]) is None
    assert worst([]) is None


def test_judge() -> None:
    assert judge(None, 5) == "unknown"
    assert judge(0, 5) == "empty" and judge(-1, None) == "empty"
    assert judge(4_999_999, 5_000_000) == "low" and judge(5_000_000, 5_000_000) == "ok"
    assert judge(1, None) == "ok"


@pytest.fixture(autouse=True)
def _clean_cache() -> Iterator[None]:
    pb.reset_cache()
    yield
    pb.reset_cache()


class _Source(pb.BalanceSource):
    provider = "openai"

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def configured(self, settings: Settings, row: Any) -> bool:
        return True

    async def read(
        self, client: httpx.AsyncClient, *, key: str, now: datetime
    ) -> pb.ProviderReading:
        self.calls += 1
        out = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(out, Exception):
            raise out
        return out  # type: ignore[no-any-return]


READING = pb.ProviderReading(None, None, None, None, 1, 2, 3)


async def test_source_error_is_fail_soft_and_cached_for_a_minute(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(pb.time, "monotonic", lambda: clock[0])
    src = _Source([httpx.ConnectError("https://x/?k=sk-secret"), READING])
    async with httpx.AsyncClient() as c:
        assert await pb.read_cached(src, c, key="sk-secret", now=NOW) == (None, "ConnectError")
        assert await pb.read_cached(src, c, key="sk-secret", now=NOW) == (None, "ConnectError")
        assert src.calls == 1
        assert "sk-secret" not in caplog.text
        clock[0] += 61
        assert (await pb.read_cached(src, c, key="sk-secret", now=NOW))[0] is READING


async def test_success_cached_five_minutes_refresh_bypasses_and_key_change_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(pb.time, "monotonic", lambda: clock[0])
    src = _Source([READING])
    async with httpx.AsyncClient() as c:
        await pb.read_cached(src, c, key="k1", now=NOW)
        clock[0] += 299
        await pb.read_cached(src, c, key="k1", now=NOW)
        assert src.calls == 1
        await pb.read_cached(
            src, c, key="k2", now=NOW
        )  # a changed key is not served the old figure
        assert src.calls == 2
        await pb.read_cached(src, c, key="k1", now=NOW, refresh=True)
        assert src.calls == 3
        clock[0] += 301
        await pb.read_cached(src, c, key="k1", now=NOW)
        assert src.calls == 4


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_Rows":
        return self

    def all(self) -> list[Any]:
        return self._rows

    def one(self) -> tuple[int, int]:
        return self._rows[0]  # type: ignore[no-any-return]


class _Session:
    """Answers the settings select and the usage sum; nothing else is asked."""

    def __init__(self, rows: list[ProviderBalanceSetting], spend: tuple[int, int]) -> None:
        self.rows, self.spend = rows, spend

    async def execute(self, stmt: Any) -> _Rows:
        if "provider_balance_settings" in str(stmt):
            return _Rows(self.rows)
        return _Rows([self.spend])


async def test_aggregator_statuses_for_a_mixed_install() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        host = req.url.host
        if host == "openrouter.ai":
            if req.url.path.endswith("/key"):
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "limit": 50,
                            "limit_remaining": 2.0,
                            "limit_reset": "daily",
                            "usage": 48,
                            "usage_daily": 1,
                            "usage_weekly": 3,
                            "usage_monthly": 10,
                        }
                    },
                )
            return httpx.Response(403)
        if host == "api.openai.com":
            return httpx.Response(200, json=OPENAI_PAGE_2)
        return httpx.Response(500)

    settings = Settings(
        llm_provider="openrouter", llm_api_key="sk-or-x", openai_admin_key="sk-admin-y"
    )
    groq = setting(
        id=None,
        manual_balance_usd_micro=10_000_000,
        manual_balance_at=datetime(2026, 10, 2, tzinfo=UTC),
        low_threshold_usd_micro=20_000_000,
    )
    out = await provider_balances(
        settings,
        _Session([groq], (1_500_000, 0)),  # type: ignore[arg-type]
        transport=httpx.MockTransport(handler),
        now=NOW,
    )
    by = {b.label: b for b in out}
    assert list(by) == ["OpenRouter", "OpenAI", "Anthropic", "Groq"]
    assert by["OpenRouter"].status == "low"  # 2.0 left < the default 5.0
    assert by["OpenRouter"].remaining_usd_micro == 2_000_000
    assert by["OpenRouter"].recorded_month_usd_micro == 1_500_000  # the engine's own provider
    assert by["OpenAI"].status == "unknown"  # official spend only, no balance
    assert by["OpenAI"].reading is not None and by["OpenAI"].admin_key_set is True
    assert by["Anthropic"].status == "not_configured"
    assert by["Groq"].status == "low" and by["Groq"].spend_tracked is False
    assert by["Groq"].manual_remaining_usd_micro == 10_000_000
    assert worst(out).label == "OpenRouter"  # type: ignore[union-attr]


async def test_aggregator_survives_a_failing_provider() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    settings = Settings(llm_provider="openai", llm_api_key="sk-x", openai_admin_key="sk-admin")
    out = await provider_balances(
        settings,
        _Session([], (0, 0)),
        transport=httpx.MockTransport(handler),
        now=NOW,  # type: ignore[arg-type]
    )
    openai = next(b for b in out if b.provider == "openai")
    assert openai.status == "unknown" and openai.error == "HTTPStatusError"


async def test_anthropic_asks_up_to_now_and_counts_todays_bucket() -> None:
    seen: list[httpx.Request] = []
    today_page = {
        "data": [
            {
                "starting_at": "2026-10-04T00:00:00Z",
                "ending_at": "2026-10-05T00:00:00Z",
                "results": [
                    {"amount": "250", "currency": "USD", "model": "claude-haiku-4-5"},
                ],
            }
        ],
        "has_more": False,
    }

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=today_page)

    async with client_for(handler) as client:
        reading = await AnthropicCostSource().read(client, key="sk-ant-admin-x", now=NOW)
    # `ending_at` is exclusive: tomorrow's midnight could drop today's bucket, so it is not sent.
    assert "ending_at" not in seen[0].url.params
    assert reading.spend_today_usd_micro == 2_500_000
    assert reading.spend_week_usd_micro == reading.spend_month_usd_micro == 2_500_000


class _GatedSource(pb.BalanceSource):
    provider = "openai"

    def __init__(self, gate: Any = None) -> None:
        self.gate = gate
        self.calls = 0

    def configured(self, settings: Any, row: Any) -> bool:
        return True

    async def read(self, client: Any, *, key: str, now: datetime) -> pb.ProviderReading:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        return pb.ProviderReading(None, None, None, None, 0, 0, 0)


async def test_a_slow_provider_does_not_hold_up_another() -> None:
    import asyncio

    pb.reset_cache()
    gate = asyncio.Event()
    slow, fast = _GatedSource(gate), _GatedSource()
    async with client_for(lambda r: httpx.Response(200)) as client:
        stuck = asyncio.create_task(pb.read_cached(slow, client, key="a", now=NOW))
        await asyncio.sleep(0)  # the slow read now holds ITS lock
        got = await asyncio.wait_for(pb.read_cached(fast, client, key="b", now=NOW), 1)
        assert got[1] is None and not stuck.done()
        gate.set()
        await stuck
    pb.reset_cache()


async def test_a_timeout_is_cached_as_a_failure_so_the_next_poll_is_instant() -> None:
    import asyncio

    pb.reset_cache()
    slow = _GatedSource(asyncio.Event())  # never set: the read never finishes
    async with client_for(lambda r: httpx.Response(200)) as client:
        first = await pb.read_cached(slow, client, key="a", now=NOW, timeout=0.05)
        second = await pb.read_cached(slow, client, key="a", now=NOW, timeout=0.05)
    assert first == (None, "TimeoutError") and second == first
    assert slow.calls == 1  # the second poll came from the cached failure
    pb.reset_cache()
