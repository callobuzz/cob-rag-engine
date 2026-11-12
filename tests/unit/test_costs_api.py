"""Consumption (the header), the admin Costs report and CSV, and the OpenRouter balance."""

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.analytics import openrouter_balance as orb
from callobuzz_rag.analytics.consumption import (
    VIEW_BYOK,
    VIEW_OPERATOR_KEY,
    _safe_cell,
    costs_csv,
    tenant_consumption,
)
from callobuzz_rag.api.app import create_app
from callobuzz_rag.core.config import Settings
from tests.unit.test_usage import _app, _session_override


def _row(key, requests=1, cost_micro=0, unpriced=0):
    return {
        "key": key,
        "requests": requests,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "cost_usd": cost_micro / 1_000_000,
        "unpriced_requests": unpriced,
        "cost_known": unpriced == 0,
    }


class TestOpenRouterBalance:
    def setup_method(self) -> None:
        orb.reset_cache()

    def _settings(self, **kw):
        base = {
            "_env_file": None,
            "admin_api_key": "adm",
            "llm_provider": "openrouter",
            "llm_api_key": "sk-or-test",
        }
        return Settings(**{**base, **kw})

    def test_applies_only_to_an_openrouter_engine_key(self) -> None:
        assert orb.applies(self._settings())
        assert not orb.applies(self._settings(llm_provider="openai"))
        assert not orb.applies(self._settings(llm_api_key=""))
        assert not orb.applies(self._settings(llm_api_base="http://proxy.local"))

    def test_key_limit_remaining_wins_and_flags_low(self) -> None:
        b = orb._parse({"limit": 20, "limit_remaining": 3.5, "usage": 16.5}, None, 5.0)
        assert b.remaining_usd == 3.5 and b.low is True and b.limit_usd == 20

    def test_credits_fill_in_when_the_key_has_no_limit(self) -> None:
        b = orb._parse(
            {"limit": None, "limit_remaining": None},
            {"total_credits": 50, "total_usage": 12.5},
            5.0,
        )
        assert b.remaining_usd == 37.5 and b.low is False

    def test_unknown_balance_never_warns(self) -> None:
        b = orb._parse({"usage": 4.2}, None, 5.0)
        assert b.remaining_usd is None and b.low is False and b.usage_usd == 4.2

    async def test_answer_is_cached_five_minutes(self) -> None:
        fetch = AsyncMock(return_value=orb._parse({"limit_remaining": 40}, None, 5.0))
        with patch.object(orb, "_fetch", fetch):
            a = await orb.get_openrouter_balance(self._settings())
            b = await orb.get_openrouter_balance(self._settings())
        assert a is b and fetch.await_count == 1

    async def test_a_failure_is_unavailable_not_an_error_and_is_cached(self) -> None:
        fetch = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.object(orb, "_fetch", fetch):
            a = await orb.get_openrouter_balance(self._settings())
            await orb.get_openrouter_balance(self._settings())
        assert a is not None and a.available is False and a.low is False
        assert fetch.await_count == 1

    async def test_not_openrouter_means_none_and_no_call(self) -> None:
        fetch = AsyncMock()
        with patch.object(orb, "_fetch", fetch):
            assert await orb.get_openrouter_balance(self._settings(llm_provider="openai")) is None
        fetch.assert_not_awaited()


class TestTenantConsumption:
    async def test_customer_on_the_operator_key_sees_questions_against_the_cap(self) -> None:
        rows = [_row("operator", requests=412, cost_micro=9_000_000)]
        with (
            patch("callobuzz_rag.analytics.consumption.usage_report", AsyncMock(return_value=rows)),
            patch(
                "callobuzz_rag.analytics.consumption.question_quota",
                AsyncMock(return_value=(1000, 412)),
            ),
        ):
            out = await tenant_consumption(AsyncMock(), uuid.uuid4(), date(2026, 10, 2))
        assert out["view"] == VIEW_OPERATOR_KEY
        assert out["questions"] == 412 and out["question_limit"] == 1000
        assert out["spend_usd"] is None  # the operator's cost is not the customer's business

    async def test_unlimited_plan_is_just_the_count(self) -> None:
        with (
            patch(
                "callobuzz_rag.analytics.consumption.usage_report",
                AsyncMock(return_value=[_row("operator", 7)]),
            ),
            patch(
                "callobuzz_rag.analytics.consumption.question_quota", AsyncMock(return_value=None)
            ),
            patch(
                "callobuzz_rag.analytics.consumption.questions_this_month",
                AsyncMock(return_value=7),
            ),
        ):
            out = await tenant_consumption(AsyncMock(), uuid.uuid4(), date(2026, 10, 2))
        assert out["questions"] == 7 and out["question_limit"] is None

    async def test_byok_customer_sees_their_own_spend(self) -> None:
        rows = [_row("byok", requests=30, cost_micro=2_500_000)]
        with (
            patch("callobuzz_rag.analytics.consumption.usage_report", AsyncMock(return_value=rows)),
            patch(  # P5 plans: the plan includes the own key
                "callobuzz_rag.analytics.consumption.byok_allowed", AsyncMock(return_value=True)
            ),
        ):
            out = await tenant_consumption(AsyncMock(), uuid.uuid4(), date(2026, 10, 2))
        assert out["view"] == VIEW_BYOK
        assert out["spend_usd"] == pytest.approx(2.5) and out["spend_known"] is True

    async def test_byok_spend_with_an_unpriced_request_says_so(self) -> None:
        rows = [_row("byok", requests=3, cost_micro=1_000_000, unpriced=1)]
        with (
            patch("callobuzz_rag.analytics.consumption.usage_report", AsyncMock(return_value=rows)),
            patch(  # P5 plans: the plan includes the own key
                "callobuzz_rag.analytics.consumption.byok_allowed", AsyncMock(return_value=True)
            ),
        ):
            out = await tenant_consumption(AsyncMock(), uuid.uuid4(), date(2026, 10, 2))
        assert out["spend_known"] is False and out["unpriced_requests"] == 1

    async def test_mixed_payers_stay_on_the_questions_view(self) -> None:
        rows = [_row("byok", 3, 1_000), _row("operator", 5)]
        with (
            patch("callobuzz_rag.analytics.consumption.usage_report", AsyncMock(return_value=rows)),
            patch(
                "callobuzz_rag.analytics.consumption.question_quota",
                AsyncMock(return_value=(100, 8)),
            ),
        ):
            out = await tenant_consumption(AsyncMock(), uuid.uuid4(), date(2026, 10, 2))
        assert out["view"] == VIEW_OPERATOR_KEY


class TestConsumptionEndpoint:
    async def test_admin_gets_the_platform_view_with_the_openrouter_balance(self) -> None:
        app, restore = _app(admin=True)
        balance = orb.OpenRouterBalance(
            remaining_usd=3.2, limit_usd=None, usage_usd=40.0, usage_monthly_usd=12.0, low=True
        )
        platform = {
            "view": "platform",
            "spend_usd": 12.34,
            "spend_known": True,
            "unpriced_requests": 0,
            "questions": 90,
            "question_limit": None,
        }
        try:
            _session_override(app, [])
            with (
                patch(
                    "callobuzz_rag.api.routes.usage.platform_consumption",
                    AsyncMock(return_value=platform),
                ),
                patch(
                    "callobuzz_rag.api.routes.usage.get_openrouter_balance",
                    AsyncMock(return_value=balance),
                ),
            ):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                    res = await c.get("/api/v1/usage/consumption")
        finally:
            restore()
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["view"] == "platform" and body["spend_usd"] == 12.34
        assert body["usd_inr_rate"] == 84.0
        assert body["openrouter"]["remaining_usd"] == 3.2 and body["openrouter"]["low"] is True
        assert body["openrouter"]["low_threshold_usd"] == 5.0
        assert body["month"] == datetime.now(UTC).date().replace(day=1).isoformat()

    async def test_the_operator_workspace_is_the_platform_view_too(self) -> None:
        tenant = SimpleNamespace(id=uuid.uuid4(), is_operator=True)
        app, restore = _app(tenant=tenant)
        platform = {
            "view": "platform",
            "spend_usd": 1.0,
            "spend_known": True,
            "unpriced_requests": 0,
            "questions": 1,
            "question_limit": None,
        }
        try:
            session = _session_override(app, [])
            session.get = AsyncMock(return_value=tenant)
            with (
                patch(
                    "callobuzz_rag.api.routes.usage.platform_consumption",
                    AsyncMock(return_value=platform),
                ),
                patch(
                    "callobuzz_rag.api.routes.usage.get_openrouter_balance",
                    AsyncMock(return_value=None),
                ),
            ):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                    res = await c.get("/api/v1/usage/consumption")
        finally:
            restore()
        assert res.status_code == 200 and res.json()["view"] == "platform"
        assert res.json()["openrouter"] is None

    async def test_a_customer_never_gets_the_balance_or_the_platform_cost(self) -> None:
        tenant = SimpleNamespace(id=uuid.uuid4(), is_operator=False)
        app, restore = _app(tenant=tenant)
        mine = {
            "view": "operator_key",
            "spend_usd": None,
            "spend_known": True,
            "unpriced_requests": 0,
            "questions": 412,
            "question_limit": 1000,
        }
        balance = AsyncMock()
        try:
            session = _session_override(app, [])
            session.get = AsyncMock(return_value=tenant)
            with (
                patch(
                    "callobuzz_rag.api.routes.usage.tenant_consumption",
                    AsyncMock(return_value=mine),
                ) as tc,
                patch("callobuzz_rag.api.routes.usage.platform_consumption", AsyncMock()) as pc,
                patch("callobuzz_rag.api.routes.usage.get_openrouter_balance", balance),
            ):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                    res = await c.get("/api/v1/usage/consumption")
        finally:
            restore()
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["view"] == "operator_key" and body["questions"] == 412
        assert body["question_limit"] == 1000 and body["openrouter"] is None
        pc.assert_not_awaited()
        balance.assert_not_awaited()
        assert tc.await_args.args[1] == tenant.id

    async def test_unauthenticated_is_refused(self) -> None:
        app = create_app()
        app.state.settings = Settings(_env_file=None, admin_api_key="adm")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            res = await c.get("/api/v1/usage/consumption")
        assert res.status_code == 401


class TestUsageByPayerAndModel:
    async def test_group_by_model_and_payer_filter_reach_the_query(self) -> None:
        app, restore = _app(admin=True)
        try:
            session = _session_override(app, [])
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                res = await c.get("/api/v1/usage?group_by=model&payer=byok")
                bad = await c.get("/api/v1/usage?group_by=nonsense")
                bad_payer = await c.get("/api/v1/usage?payer=guest")
        finally:
            restore()
        assert res.status_code == 200 and bad.status_code == 422 and bad_payer.status_code == 422
        sql = str(
            session.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True})
        )
        assert "GROUP BY usage_daily.model" in sql and "usage_daily.payer = 'byok'" in sql


class TestAdminCosts:
    async def test_only_the_admin_may_read_costs(self) -> None:
        tenant = SimpleNamespace(id=uuid.uuid4(), is_operator=False)
        app, restore = _app(tenant=tenant)
        try:
            _session_override(app, [])
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                res = await c.get("/api/v1/admin/costs")
                csv_res = await c.get("/api/v1/admin/costs/export")
        finally:
            restore()
        assert res.status_code == 403 and csv_res.status_code == 403

    async def test_costs_report_shape(self) -> None:
        app, restore = _app(admin=True)
        tenant_id = str(uuid.uuid4())

        async def fake_report(session, *, group_by, **_kw):
            if group_by == "tenant":
                return [{**_row(tenant_id, 5, 2_000_000), "is_operator": False}]
            return [_row("x", 5, 2_000_000)]

        try:
            _session_override(app, [])
            with (
                patch("callobuzz_rag.api.routes.costs.usage_report", side_effect=fake_report),
                patch(
                    "callobuzz_rag.api.routes.costs.with_labels",
                    AsyncMock(
                        side_effect=lambda s, g, rows: [{**r, "label": "Acme"} for r in rows]
                    ),
                ),
                patch(
                    "callobuzz_rag.api.routes.costs.get_openrouter_balance",
                    AsyncMock(return_value=None),
                ),
            ):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                    res = await c.get("/api/v1/admin/costs?start=2026-10-01&end=2026-10-02")
                    reversed_range = await c.get(
                        "/api/v1/admin/costs?start=2026-10-05&end=2026-10-02"
                    )
        finally:
            restore()
        assert res.status_code == 200, res.text
        body = res.json()
        for key in (
            "today",
            "month",
            "range",
            "by_tenant",
            "by_corpus",
            "by_model",
            "by_source",
            "by_payer",
        ):
            assert key in body
        assert body["range"]["cost_usd"] == 2.0 and body["by_tenant"][0]["label"] == "Acme"
        assert body["openrouter"] is None
        assert (
            reversed_range.status_code == 422 and reversed_range.json()["code"] == "invalid_range"
        )

    async def test_csv_download(self) -> None:
        app, restore = _app(admin=True)
        try:
            _session_override(app, [])
            with patch(
                "callobuzz_rag.api.routes.costs.costs_csv",
                AsyncMock(return_value="day,customer\r\n"),
            ):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                    res = await c.get("/api/v1/admin/costs/export?start=2026-10-01&end=2026-10-02")
        finally:
            restore()
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/csv")
        assert "costs-2026-10-01-to-2026-10-02.csv" in res.headers["content-disposition"]
        assert res.headers["cache-control"] == "no-store"


class TestCostsCsv:
    def test_formula_cells_are_defused(self) -> None:
        assert _safe_cell("=SUM(A1)") == "'=SUM(A1)"
        assert _safe_cell("@x") == "'@x" and _safe_cell("+1") == "'+1"
        assert _safe_cell("Acme") == "Acme"

    async def test_rows_carry_both_currencies_and_the_unknown_price_count(self) -> None:
        row = SimpleNamespace(
            day=date(2026, 10, 1),
            customer="=Evil",
            knowledge_base="Help centre",
            source="stream",
            payer="operator",
            model="openai/gpt-oss-120b",
            requests=4,
            prompt_tokens=100,
            completion_tokens=50,
            reasoning_tokens=10,
            cost_micro=1_500_000,
            unpriced_requests=1,
        )
        result = MagicMock()
        result.all.return_value = [row]
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)
        text = await costs_csv(session, date(2026, 10, 1), date(2026, 10, 2), 84.0)
        lines = text.strip().split("\r\n")
        assert lines[0].startswith("day,customer,knowledge_base,source,payer,model")
        assert lines[1] == (
            "2026-10-01,'=Evil,Help centre,stream,operator,openai/gpt-oss-120b,4,100,50,10,"
            "1.500000,126.0000,1"
        )


class TestConsumptionScope:
    """The header summary is for a workspace or admin credential, never a knowledge-base key."""

    async def test_a_corpus_key_is_refused_even_on_the_operator_workspace(self) -> None:
        tenant_id = uuid.uuid4()
        corpus = SimpleNamespace(id=uuid.uuid4(), tenant_id=tenant_id)
        app, restore = _app(corpus)
        operator = SimpleNamespace(id=tenant_id, is_operator=True)
        try:
            session = _session_override(app, [])
            session.get = AsyncMock(return_value=operator)
            with (
                patch("callobuzz_rag.api.routes.usage.platform_consumption", AsyncMock()) as pc,
                patch("callobuzz_rag.api.routes.usage.tenant_consumption", AsyncMock()) as tc,
                patch("callobuzz_rag.api.routes.usage.get_openrouter_balance", AsyncMock()) as bal,
            ):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                    res = await c.get("/api/v1/usage/consumption")
        finally:
            restore()
        assert res.status_code == 403
        assert res.json()["code"] == "scope_forbidden"
        pc.assert_not_awaited()
        tc.assert_not_awaited()
        bal.assert_not_awaited()

    async def test_a_corpus_key_never_sees_the_tenants_spend(self) -> None:
        corpus = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
        app, restore = _app(corpus)
        try:
            _session_override(app, [])
            with patch("callobuzz_rag.api.routes.usage.tenant_consumption", AsyncMock()) as tc:
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                    res = await c.get("/api/v1/usage/consumption")
        finally:
            restore()
        assert res.status_code == 403
        tc.assert_not_awaited()

    async def test_a_session_without_a_workspace_gets_a_coded_error(self) -> None:
        tenant = SimpleNamespace(id=None, is_operator=False)
        app, restore = _app(tenant=tenant)
        try:
            _session_override(app, [])
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                res = await c.get("/api/v1/usage/consumption")
        finally:
            restore()
        assert res.status_code == 403 and res.json()["code"] == "no_workspace"


class TestCustomersNeverReadOperatorCost:
    async def test_a_tenant_sees_cost_only_from_its_own_key(self) -> None:
        tenant = SimpleNamespace(id=uuid.uuid4(), is_operator=False)
        app, restore = _app(tenant=tenant)
        calls: list[str | None] = []

        async def fake_report(session, *, payer=None, group_by="day", **_kw):
            calls.append(payer)
            if payer == "byok":
                return [_row("2026-10-01", 2, 500_000)]
            return [_row("2026-10-01", 5, 9_000_000, unpriced=1)]

        try:
            session = _session_override(app, [])
            session.get = AsyncMock(return_value=tenant)
            with (
                patch("callobuzz_rag.api.routes.usage.usage_report", side_effect=fake_report),
                patch(
                    "callobuzz_rag.api.routes.usage.authorized_corpus_ids",
                    AsyncMock(return_value=[uuid.uuid4()]),
                ),
            ):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                    res = await c.get("/api/v1/usage")
        finally:
            restore()
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["rows"][0]["requests"] == 5  # every question still counts
        assert body["rows"][0]["cost_usd"] == 0.5  # the operator's 9.0 is not theirs
        assert body["rows"][0]["unpriced_requests"] == 0 and body["rows"][0]["cost_known"] is True
        assert body["totals"]["cost_usd"] == 0.5
        assert calls == [None, "byok"]

    async def test_a_row_with_no_own_key_spend_shows_zero_not_the_operators(self) -> None:
        tenant = SimpleNamespace(id=uuid.uuid4(), is_operator=False)
        app, restore = _app(tenant=tenant)

        async def fake_report(session, *, payer=None, **_kw):
            return [] if payer == "byok" else [_row("2026-10-01", 5, 9_000_000)]

        try:
            session = _session_override(app, [])
            session.get = AsyncMock(return_value=tenant)
            with (
                patch("callobuzz_rag.api.routes.usage.usage_report", side_effect=fake_report),
                patch(
                    "callobuzz_rag.api.routes.usage.authorized_corpus_ids",
                    AsyncMock(return_value=[uuid.uuid4()]),
                ),
            ):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                    res = await c.get("/api/v1/usage")
        finally:
            restore()
        assert res.json()["rows"][0]["cost_usd"] == 0.0

    async def test_the_admin_and_the_operator_workspace_see_everything(self) -> None:
        operator = SimpleNamespace(id=uuid.uuid4(), is_operator=True)
        for kwargs in ({"admin": True}, {"tenant": operator}):
            app, restore = _app(**kwargs)
            calls: list[str | None] = []

            async def fake_report(session, *, payer=None, _calls=calls, **_kw):
                _calls.append(payer)
                return [_row("2026-10-01", 5, 9_000_000)]

            try:
                session = _session_override(app, [])
                session.get = AsyncMock(return_value=operator)
                with (
                    patch("callobuzz_rag.api.routes.usage.usage_report", side_effect=fake_report),
                    patch(
                        "callobuzz_rag.api.routes.usage.authorized_corpus_ids",
                        AsyncMock(return_value=None),
                    ),
                ):
                    async with AsyncClient(
                        transport=ASGITransport(app=app), base_url="http://t"
                    ) as c:
                        res = await c.get("/api/v1/usage")
            finally:
                restore()
            assert res.json()["rows"][0]["cost_usd"] == 9.0
            assert calls == [None]
