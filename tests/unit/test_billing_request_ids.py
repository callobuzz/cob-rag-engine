"""BIL3: the query routes meter under stable request ids, and mark the admin principal."""
# ruff: noqa: F811

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient

from callobuzz_rag.billing.context import ADMIN_ANSWER
from callobuzz_rag.llm.cost import CallCost, CostLedger
from tests.unit.test_query_stream import (  # noqa: F401
    ADMIN,
    _events,
    _orchestrator,
    app,
    corpus,
    log_session,
)


async def _stream(app, corpus, orch, recorded: list[dict]) -> str:  # type: ignore[no-untyped-def]
    async def fake_record(_factory, **kwargs):  # type: ignore[no-untyped-def]
        recorded.append({**kwargs, "admin": ADMIN_ANSWER.get()})

    with (
        patch("callobuzz_rag.api.routes.query._build_orchestrator", return_value=orch),
        patch("callobuzz_rag.api.routes.query.record_usage_new_session", fake_record),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            res = await c.post(
                "/api/v1/query/stream",
                headers=ADMIN,
                json={"query": "Can I return this?", "corpus_id": str(corpus.id)},
            )
    assert res.status_code == 200, res.text
    return res.text


async def test_a_streamed_answer_is_metered_under_its_query_id_and_marked_admin(
    app, corpus
) -> None:
    orch, _c, _d = _orchestrator(["Yes."])
    recorded: list[dict] = []
    text = await _stream(app, corpus, orch, recorded)
    query_id = dict(_events(text))["sources"]["query_id"]
    assert recorded[0]["request_id"] == uuid.UUID(query_id)
    assert recorded[0]["admin"] is True  # the admin key: counted, never billed to the customer


async def test_a_failed_stream_meters_under_a_derived_id(app, corpus) -> None:
    orch, _c, _d = _orchestrator(["partial"], fail=True)
    ledger = CostLedger()
    ledger.add(CallCost("openai/gpt-oss-20b", 40, 8, 0, 0.00004))
    orch.llm.cost_ledger = ledger
    recorded: list[dict] = []
    text = await _stream(app, corpus, orch, recorded)
    query_id = uuid.UUID(dict(_events(text))["sources"]["query_id"])
    assert recorded[0]["requests"] == 0
    assert recorded[0]["request_id"] == uuid.uuid5(query_id, "failed")


async def test_every_cache_hit_is_a_new_billed_answer(app, corpus) -> None:
    cache = MagicMock()
    cache.get = AsyncMock(
        return_value={
            "answer": "From the cache",
            "citations": [],
            "confidence": 1.0,
            "query_id": str(uuid.uuid4()),
            "model_used": "m",
        }
    )
    record = AsyncMock()
    with (
        patch("callobuzz_rag.api.routes.query._get_query_cache", return_value=cache),
        patch("callobuzz_rag.api.routes.query.record_usage", record),
        patch("callobuzz_rag.api.routes.query.counts_cached_answers", AsyncMock(return_value=True)),
        patch("callobuzz_rag.fairuse.gate.enforce_question_cap", AsyncMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            for _ in range(2):
                res = await c.post(
                    "/api/v1/query",
                    headers=ADMIN,
                    json={"query": "q", "corpus_id": str(corpus.id)},
                )
                assert res.status_code == 200, res.text
    ids = [call.kwargs["request_id"] for call in record.await_args_list]
    assert len(ids) == 2 and ids[0] != ids[1] and all(isinstance(i, uuid.UUID) for i in ids)


async def test_a_fresh_answer_is_metered_under_the_query_id_the_client_gets(app, corpus) -> None:
    query_id = uuid.uuid4()
    orch = MagicMock()
    orch.llm = MagicMock()
    orch.llm.cost_ledger = None
    orch.query = AsyncMock(
        return_value={
            "answer": "Yes",
            "citations": [],
            "confidence": 0.9,
            "query_id": query_id,
            "model_used": "m",
            "latency_ms": 5,
            "token_usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
    )
    record = AsyncMock()
    with (
        patch("callobuzz_rag.api.routes.query._build_orchestrator", return_value=orch),
        patch("callobuzz_rag.api.routes.query.record_usage", record),
        patch("callobuzz_rag.fairuse.gate.enforce_question_cap", AsyncMock()),
        patch("callobuzz_rag.api.routes.query.route_answer_llm", return_value=(orch.llm, None)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            res = await c.post(
                "/api/v1/query",
                headers=ADMIN,
                json={"query": "q", "corpus_id": str(corpus.id)},
            )
    assert res.status_code == 200, res.text
    assert record.await_args.kwargs["request_id"] == query_id


async def test_guided_setup_spend_is_waived_for_the_operator_only() -> None:
    from types import SimpleNamespace

    from callobuzz_rag.conversation.setup.service import SetupService

    seen: list[bool] = []

    async def fake_record(_session, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(ADMIN_ANSWER.get())

    corpus = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
    for is_admin in (True, False):
        request = SimpleNamespace(state=SimpleNamespace(is_admin=is_admin))
        service = SetupService(MagicMock(), request)  # type: ignore[arg-type]
        ADMIN_ANSWER.set(not is_admin)
        with (
            patch("callobuzz_rag.analytics.usage.record_usage", fake_record),
            patch("callobuzz_rag.llm.cost.request_cost_of", return_value="COST"),
            patch("callobuzz_rag.analytics.usage.payer_of", return_value="operator"),
        ):
            await service._meter(corpus, MagicMock(), SimpleNamespace(model="m"))  # type: ignore[arg-type]
    assert seen == [True, False]
