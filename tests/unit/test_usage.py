"""Usage metering: the upsert, the report, the scoped endpoint, the wiring."""

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from callobuzz_rag.analytics.usage import (
    estimate_tokens,
    record_usage,
    record_usage_new_session,
    usage_report,
    widget_source,
)
from callobuzz_rag.api.app import create_app
from callobuzz_rag.core.config import Settings


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


class TestRecordUsage:
    async def test_is_an_atomic_upsert_with_cost(self) -> None:
        session = AsyncMock()
        corpus_id = uuid.uuid4()
        await record_usage(
            session,
            corpus_id=corpus_id,
            tenant_id=uuid.uuid4(),
            source="api",
            prompt_tokens=1000,
            completion_tokens=500,
            model="openai/gpt-4o-mini",
        )
        stmt = session.execute.await_args_list[0].args[0]
        sql = _sql(stmt)
        assert "INSERT INTO usage_daily" in sql
        assert (
            "ON CONFLICT ON CONSTRAINT uq_usage_daily_day_corpus_tenant_source_payer_model DO UPDATE"
            in sql
        )
        assert "requests = (usage_daily.requests + " in sql  # incremented, not overwritten
        params = stmt.compile(dialect=postgresql.dialect()).params
        # gpt-4o-mini: $0.15 / $0.60 per 1M tokens, stored as integer micro-dollars
        assert params["cost_micro"] == 450
        assert params["unpriced_requests"] == 0
        assert params["payer"] == "operator" and params["model"] == "openai/gpt-4o-mini"
        session.commit.assert_awaited_once()

    async def test_openrouter_prefix_prices_like_the_bare_model(self) -> None:
        session = AsyncMock()
        await record_usage(
            session,
            corpus_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            source="api",
            prompt_tokens=1000,
            completion_tokens=0,
            model="openrouter/openai/gpt-4o-mini",
        )
        daily = session.execute.await_args_list[0].args[0]
        params = daily.compile(dialect=postgresql.dialect()).params
        assert params["cost_micro"] > 0
        assert params["model"] == "openai/gpt-4o-mini"  # one row per model, prefix or not

    async def test_an_unpriced_model_is_unknown_never_free(self) -> None:
        session = AsyncMock()
        await record_usage(
            session,
            corpus_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            source="api",
            prompt_tokens=1000,
            completion_tokens=200,
            model="acme/unheard-of-1",
        )
        params = (
            session.execute.await_args_list[0].args[0].compile(dialect=postgresql.dialect()).params
        )
        assert params["cost_micro"] == 0
        assert params["unpriced_requests"] == 1  # flagged, not a silent zero
        sql = _sql(session.execute.await_args_list[0].args[0])
        assert "unpriced_requests = (usage_daily.unpriced_requests + " in sql

    async def test_the_request_cost_replaces_the_estimate_and_carries_the_payer(self) -> None:
        from callobuzz_rag.llm.cost import CallCost, CostLedger

        ledger = CostLedger()
        ledger.add(CallCost("fast", 10, 4, 0, 0.0001))
        ledger.add(CallCost("answer", 200, 80, 30, 0.0012))
        session = AsyncMock()
        await record_usage(
            session,
            corpus_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            source="stream",
            prompt_tokens=1,
            completion_tokens=1,
            model="openai/gpt-oss-120b",
            cost=ledger.summary(),
            payer="byok",
        )
        stmt = session.execute.await_args_list[0].args[0]
        params = stmt.compile(dialect=postgresql.dialect()).params
        assert params["cost_micro"] == 1300
        assert params["prompt_tokens"] == 210 and params["completion_tokens"] == 84
        assert params["reasoning_tokens"] == 30
        assert params["payer"] == "byok"
        assert params["unpriced_requests"] == 0

    async def test_a_request_with_an_unpriced_call_is_flagged(self) -> None:
        from callobuzz_rag.llm.cost import CallCost, CostLedger

        ledger = CostLedger()
        ledger.add(CallCost("answer", 200, 80, 0, None))
        session = AsyncMock()
        await record_usage(
            session,
            corpus_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            source="api",
            prompt_tokens=0,
            completion_tokens=0,
            cost=ledger.summary(),
        )
        params = (
            session.execute.await_args_list[0].args[0].compile(dialect=postgresql.dialect()).params
        )
        assert params["unpriced_requests"] == 1 and params["cost_micro"] == 0

    async def test_an_answered_question_counts_in_the_tenant_month(self) -> None:
        """accounts A6: the monthly cap's counter, keyed by tenant and UTC month."""
        session = AsyncMock()
        tenant_id = uuid.uuid4()
        await record_usage(
            session,
            corpus_id=uuid.uuid4(),
            tenant_id=tenant_id,
            source="widget:k",
            prompt_tokens=10,
            completion_tokens=5,
        )
        assert session.execute.await_count == 2
        month_stmt = session.execute.await_args_list[1].args[0]
        sql = _sql(month_stmt)
        assert "INSERT INTO tenant_question_counts" in sql
        assert "ON CONFLICT (tenant_id, month) DO UPDATE" in sql
        assert "questions = (tenant_question_counts.questions + " in sql
        params = month_stmt.compile(dialect=postgresql.dialect()).params
        assert params["tenant_id"] == tenant_id
        assert params["month"] == datetime.now(UTC).date().replace(day=1)
        assert params["questions"] == 1
        session.commit.assert_awaited_once()

    async def test_an_auxiliary_call_does_not_count_a_question(self) -> None:
        session = AsyncMock()
        await record_usage(
            session,
            corpus_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            source="conversation",
            prompt_tokens=30,
            completion_tokens=4,
            requests=0,
        )
        assert session.execute.await_count == 1  # usage_daily only
        assert "tenant_question_counts" not in _sql(session.execute.await_args.args[0])

    async def test_a_counter_failure_is_swallowed_like_the_daily_row(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[None, RuntimeError("counter down")])
        await record_usage(  # must not raise
            session,
            corpus_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            source="api",
            prompt_tokens=1,
            completion_tokens=1,
        )
        session.commit.assert_not_awaited()

    async def test_requests_zero_adds_tokens_without_a_request(self) -> None:
        """An auxiliary call (a conversation title) must not count as a request."""
        session = AsyncMock()
        await record_usage(
            session,
            corpus_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            source="conversation",
            prompt_tokens=30,
            completion_tokens=4,
            requests=0,
        )
        params = session.execute.await_args.args[0].compile(dialect=postgresql.dialect()).params
        assert params["requests"] == 0
        assert params["prompt_tokens"] == 30

    async def test_failure_is_swallowed(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=RuntimeError("db down"))
        await record_usage(  # must not raise
            session,
            corpus_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            source="api",
            prompt_tokens=1,
            completion_tokens=1,
        )

    async def test_new_session_variant_without_factory_is_a_noop(self) -> None:
        await record_usage_new_session(None, corpus_id=uuid.uuid4())

    def test_estimate_tokens(self) -> None:
        assert estimate_tokens("") == 0
        assert 3 <= estimate_tokens("hello there, world") <= 6

    def test_widget_source(self) -> None:
        key = uuid.uuid4()
        assert widget_source(key) == f"widget:{key}"


class TestUsageReport:
    async def test_empty_scope_returns_nothing_without_querying(self) -> None:
        session = AsyncMock()
        assert (
            await usage_report(
                session, corpus_ids=[], start=date.today(), end=date.today(), group_by="day"
            )
            == []
        )
        session.execute.assert_not_awaited()

    async def test_scoped_query_filters_corpora(self) -> None:
        session = AsyncMock()
        result = MagicMock()
        result.all.return_value = [
            SimpleNamespace(
                key="api",
                requests=3,
                prompt_tokens=30,
                completion_tokens=12,
                reasoning_tokens=5,
                cost_micro=500_000,
                unpriced_requests=1,
            )
        ]
        session.execute = AsyncMock(return_value=result)
        cid = uuid.uuid4()
        rows = await usage_report(
            session,
            corpus_ids=[cid],
            start=date(2026, 9, 1),
            end=date(2026, 9, 30),
            group_by="source",
        )
        assert rows == [
            {
                "key": "api",
                "requests": 3,
                "prompt_tokens": 30,
                "completion_tokens": 12,
                "reasoning_tokens": 5,
                "cost_usd": 0.5,
                "unpriced_requests": 1,
                "cost_known": False,
            }
        ]
        sql = _sql(session.execute.await_args.args[0])
        assert "usage_daily.corpus_id IN" in sql
        assert "GROUP BY usage_daily.source" in sql
        assert "is_operator" not in sql  # raw accounting is untouched by default

    async def test_tenant_rows_say_which_one_is_the_operator_workspace(self) -> None:
        house, customer = uuid.uuid4(), uuid.uuid4()
        usage = MagicMock()
        usage.all.return_value = [
            SimpleNamespace(
                key=key,
                requests=1,
                prompt_tokens=1,
                completion_tokens=1,
                reasoning_tokens=0,
                cost_micro=100_000,
                unpriced_requests=0,
            )
            for key in (house, customer)
        ]
        operators = MagicMock()
        operators.scalars.return_value.all.return_value = [house]
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[usage, operators])
        rows = await usage_report(
            session,
            corpus_ids=None,
            start=date(2026, 9, 1),
            end=date(2026, 9, 30),
            group_by="tenant",
        )
        assert [(r["key"], r["is_operator"]) for r in rows] == [
            (str(house), True),
            (str(customer), False),
        ]

    async def test_exclude_operator_filters_in_sql(self) -> None:
        result = MagicMock()
        result.all.return_value = []
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)
        await usage_report(
            session,
            corpus_ids=None,
            start=date(2026, 9, 1),
            end=date(2026, 9, 30),
            group_by="day",
            exclude_operator=True,
        )
        sql = _sql(session.execute.await_args.args[0])
        assert "usage_daily.tenant_id NOT IN" in sql and "tenants.is_operator" in sql


def _app(state_corpus=None, *, admin: bool = False, tenant=None):
    app = create_app()
    app.state.settings = Settings(_env_file=None, admin_api_key="adm")
    from callobuzz_rag.api.auth import APIKeyAuthMiddleware

    original = APIKeyAuthMiddleware.dispatch

    async def fake(self, request, call_next):
        request.state.is_admin = admin
        request.state.corpus = state_corpus
        request.state.tenant = tenant
        return await call_next(request)

    APIKeyAuthMiddleware.dispatch = fake
    return app, lambda: setattr(APIKeyAuthMiddleware, "dispatch", original)


def _session_override(app, rows):
    from callobuzz_rag.api.deps import get_session

    session = AsyncMock()
    result = MagicMock()
    result.all.return_value = rows
    session.execute = AsyncMock(return_value=result)

    async def override():
        yield session

    app.dependency_overrides[get_session] = override
    return session


class TestUsageEndpoint:
    async def test_corpus_key_sees_only_itself_with_totals(self) -> None:
        corpus = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
        app, restore = _app(corpus)
        try:
            session = _session_override(
                app,
                [
                    SimpleNamespace(
                        key="2026-09-01",
                        requests=2,
                        prompt_tokens=10,
                        completion_tokens=4,
                        reasoning_tokens=0,
                        cost_micro=100_000,
                        unpriced_requests=0,
                    ),
                    SimpleNamespace(
                        key="2026-09-02",
                        requests=1,
                        prompt_tokens=5,
                        completion_tokens=2,
                        reasoning_tokens=0,
                        cost_micro=50_000,
                        unpriced_requests=1,
                    ),
                ],
            )
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                res = await c.get("/api/v1/usage?start=2026-09-01&end=2026-09-30")
        finally:
            restore()
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["totals"]["requests"] == 3
        assert body["totals"]["cost_usd"] == pytest.approx(0.15)
        assert body["totals"]["cost_known"] is False  # one request had no price
        assert body["totals"]["unpriced_requests"] == 1
        assert body["usd_inr_rate"] == 84.0
        assert str(corpus.id) in str(
            session.execute.await_args.args[0].compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )

    async def test_corpus_key_cannot_read_another_corpus(self) -> None:
        app, restore = _app(SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4()))
        try:
            _session_override(app, [])
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                res = await c.get(f"/api/v1/usage?corpus_id={uuid.uuid4()}")
        finally:
            restore()
        assert res.status_code == 403

    async def test_tenant_cannot_read_another_tenant(self) -> None:
        app, restore = _app(tenant=SimpleNamespace(id=uuid.uuid4()))
        try:
            _session_override(app, [])
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                res = await c.get(f"/api/v1/usage?tenant_id={uuid.uuid4()}")
        finally:
            restore()
        assert res.status_code == 403

    async def test_rejects_reversed_range(self) -> None:
        app, restore = _app(admin=True)
        try:
            _session_override(app, [])
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                res = await c.get("/api/v1/usage?start=2026-09-10&end=2026-09-01")
        finally:
            restore()
        assert res.status_code == 422


@pytest.mark.usefixtures("unlimited_plan")
class TestWidgetChatIsMetered:
    async def test_widget_answer_records_usage_under_its_key(self) -> None:
        from tests.unit.test_widget import _key, _orchestrator, _session, _use_session

        corpus = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            qdrant_collection="c",
            retrieval_config=None,
            llm_config=None,
            prompt_config=None,
        )
        key = _key(corpus.id)
        app = create_app()
        app.state.settings = Settings(_env_file=None, admin_api_key="adm")
        from callobuzz_rag.widget.security import LimitResult

        app.state.widget_rate_limiter = SimpleNamespace(
            check=AsyncMock(return_value=LimitResult(True))
        )
        app.state.session_factory = MagicMock()
        session = _session(key, corpus)
        tenant = SimpleNamespace(id=corpus.tenant_id, status="active")
        session.get = AsyncMock(
            side_effect=lambda model, _id: tenant if model.__name__ == "Tenant" else corpus
        )
        _use_session(app, session)
        recorder = AsyncMock()
        with (
            patch(
                "callobuzz_rag.api.routes.widget._build_orchestrator",
                return_value=_orchestrator(["Thirty ", "days."]),
            ),
            patch("callobuzz_rag.api.routes.widget._get_config_resolver") as resolver,
            patch("callobuzz_rag.api.routes.widget.record_usage_new_session", recorder),
            patch(  # PIP1: the profile overlay needs a real resolved config
                "callobuzz_rag.tiers.resolve.resolve_with_profile",
                return_value=(SimpleNamespace(model="m", language_models={}), MagicMock()),
            ),
        ):
            resolver.return_value.resolve_llm.return_value = SimpleNamespace(
                model="m", language_models={}
            )
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                res = await c.post(
                    "/api/v1/widget/chat",
                    json={"message": "returns?"},
                    headers={"X-Widget-Key": "pk_x", "Origin": "https://acme.com"},
                )
        assert res.status_code == 200
        kwargs = recorder.await_args.kwargs
        assert kwargs["source"] == f"widget:{key.id}"
        assert kwargs["tenant_id"] == corpus.tenant_id
        assert kwargs["completion_tokens"] > 0
        assert kwargs["model"] == "m"


class TestQueryLogCarriesCorpus:
    async def test_log_query_sets_corpus_id(self) -> None:
        from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator

        orch = QueryOrchestrator.__new__(QueryOrchestrator)
        orch._session = MagicMock()
        orch._session.flush = AsyncMock()
        cid = uuid.uuid4()
        await orch._log_query(
            query_id=uuid.uuid4(),
            query_text="q",
            chunk_ids=[],
            response="a",
            confidence=1.0,
            latency_ms=1,
            model_used="m",
            token_usage={},
            corpus_id=cid,
        )
        assert orch._session.add.call_args.args[0].corpus_id == cid


@pytest.mark.usefixtures("unlimited_plan")
class TestFailedStreamsStillMeterTheirSpend:
    async def test_widget_failure_after_helper_calls_records_the_spend(self) -> None:
        from callobuzz_rag.llm.cost import CallCost, CostLedger
        from tests.unit.test_widget import _key, _orchestrator, _session, _use_session

        corpus = SimpleNamespace(
            id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            qdrant_collection="c",
            retrieval_config=None,
            llm_config=None,
            prompt_config=None,
        )
        key = _key(corpus.id)
        app = create_app()
        app.state.settings = Settings(_env_file=None, admin_api_key="adm")
        from callobuzz_rag.widget.security import LimitResult

        app.state.widget_rate_limiter = SimpleNamespace(
            check=AsyncMock(return_value=LimitResult(True))
        )
        app.state.session_factory = MagicMock()
        session = _session(key, corpus)
        tenant = SimpleNamespace(id=corpus.tenant_id, status="active")
        session.get = AsyncMock(
            side_effect=lambda model, _id: tenant if model.__name__ == "Tenant" else corpus
        )
        _use_session(app, session)
        orch = _orchestrator(["Thirty "], fail=True)
        ledger = CostLedger()
        ledger.add(CallCost("m", 40, 8, 0, 0.00004))
        orch.llm.cost_ledger = ledger
        recorder = AsyncMock()
        with (
            patch("callobuzz_rag.api.routes.widget._build_orchestrator", return_value=orch),
            patch("callobuzz_rag.api.routes.widget._get_config_resolver") as resolver,
            patch("callobuzz_rag.api.routes.widget.record_usage_new_session", recorder),
            patch(  # PIP1: the profile overlay needs a real resolved config
                "callobuzz_rag.tiers.resolve.resolve_with_profile",
                return_value=(SimpleNamespace(model="m", language_models={}), MagicMock()),
            ),
        ):
            resolver.return_value.resolve_llm.return_value = SimpleNamespace(
                model="m", language_models={}
            )
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                await c.post(
                    "/api/v1/widget/chat",
                    json={"message": "returns?"},
                    headers={"X-Widget-Key": "pk_x", "Origin": "https://acme.com"},
                )
        kwargs = recorder.await_args.kwargs
        assert kwargs["requests"] == 0 and kwargs["cost"].cost_micro == 40
        assert kwargs["source"] == f"widget:{key.id}"

    async def test_conversation_stream_failure_calls_the_failure_meter(self) -> None:
        from callobuzz_rag.api.services.streaming_service import stream_message_response

        corpus_id = uuid.uuid4()
        session = AsyncMock()
        session.add = MagicMock()
        session.get = AsyncMock(
            return_value=SimpleNamespace(corpus_id=corpus_id, message_count=0, total_tokens=0)
        )

        async def tokens(_msgs):
            yield "We open "
            raise RuntimeError("provider down")

        llm = MagicMock()
        llm.complete_stream = MagicMock(side_effect=tokens)
        failed = AsyncMock()
        with patch(
            "callobuzz_rag.memory.context_builder.ConversationContextBuilder.build_context",
            AsyncMock(return_value=[{"role": "user", "content": "hours?"}]),
        ):
            _ = [
                e
                async for e in stream_message_response(
                    conversation_id=uuid.uuid4(),
                    corpus_id=corpus_id,
                    content="hours?",
                    session_factory=MagicMock(return_value=session),
                    llm_provider=llm,
                    meter_failed=failed,
                )
            ]
        failed.assert_awaited_once()
