"""The answering routes meter the request's real cost and its payer, and report the cost."""

from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from callobuzz_rag.llm.cost import CallCost, CostLedger
from tests.unit.test_query_stream import (  # noqa: F401
    ADMIN,
    _events,
    _orchestrator,
    app,
    corpus,
    log_session,
)


async def test_stream_done_and_the_meter_carry_the_real_cost(app, corpus) -> None:  # noqa: F811
    orch, _chunk, _doc = _orchestrator(["Yes, ", "within 30 days."])
    ledger = CostLedger()
    ledger.add(CallCost("openai/gpt-oss-20b", 40, 8, 0, 0.00004))
    ledger.add(CallCost("openai/gpt-oss-120b", 300, 90, 25, 0.00096))
    orch.llm.cost_ledger = ledger
    recorded: list[dict] = []

    async def fake_record(_factory, **kwargs):
        recorded.append(kwargs)

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
    done = dict(_events(res.text))["done"]
    assert done["usage"]["cost_usd"] == 0.001
    assert done["usage"]["cost_known"] is True
    assert recorded[0]["cost"].cost_micro == 1000
    assert recorded[0]["payer"] == "operator"  # no stored or sent key: the engine's own


async def test_stream_with_an_unpriced_call_reports_price_unknown(app, corpus) -> None:  # noqa: F811
    orch, _chunk, _doc = _orchestrator(["Hi"])
    ledger = CostLedger()
    ledger.add(CallCost("acme/unheard-of-1", 40, 8, 0, None))
    orch.llm.cost_ledger = ledger

    async def fake_record(_factory, **_kwargs):
        return None

    with (
        patch("callobuzz_rag.api.routes.query._build_orchestrator", return_value=orch),
        patch("callobuzz_rag.api.routes.query.record_usage_new_session", fake_record),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            res = await c.post(
                "/api/v1/query/stream",
                headers=ADMIN,
                json={"query": "q", "corpus_id": str(corpus.id)},
            )
    done = dict(_events(res.text))["done"]
    assert done["usage"]["cost_usd"] is None  # not 0: the screen says "price unknown"
    assert done["usage"]["cost_known"] is False


async def test_a_corpus_with_its_own_key_is_metered_as_byok(app, corpus) -> None:  # noqa: F811
    orch, _chunk, _doc = _orchestrator(["Hi"])
    corpus.llm_config = {"api_key_encrypted": "blob"}
    recorded: list[dict] = []

    async def fake_record(_factory, **kwargs):
        recorded.append(kwargs)

    with (
        patch("callobuzz_rag.api.routes.query._build_orchestrator", return_value=orch),
        patch("callobuzz_rag.api.routes.query.record_usage_new_session", fake_record),
        patch(
            "callobuzz_rag.core.config_resolver.ConfigResolver._decrypt_corpus_key",
            return_value="sk-theirs",
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post(
                "/api/v1/query/stream",
                headers=ADMIN,
                json={"query": "q", "corpus_id": str(corpus.id)},
            )
    assert recorded[0]["payer"] == "byok"


def test_a_cached_answer_is_a_known_free_one() -> None:
    from callobuzz_rag.llm.cost import RequestCost

    assert RequestCost().cost_usd == 0.0 and RequestCost().as_dict()["cost_known"] is True


async def test_a_cached_answer_on_a_byok_corpus_is_metered_as_byok(app, corpus) -> None:  # noqa: F811
    from unittest.mock import AsyncMock, MagicMock

    corpus.llm_config = {"api_key_encrypted": "blob"}
    cache = MagicMock()
    cache.get = AsyncMock(
        return_value={
            "answer": "From the cache",
            "citations": [],
            "confidence": 1.0,
            "query_id": "6f1c0b0e-3b53-4d0e-9a53-1a2b3c4d5e6f",
            "model_used": "openai/gpt-oss-120b",
        }
    )
    record = AsyncMock()
    with (
        patch("callobuzz_rag.api.routes.query._get_query_cache", return_value=cache),
        patch("callobuzz_rag.api.routes.query.record_usage", record),
        patch("callobuzz_rag.api.routes.query.counts_cached_answers", AsyncMock(return_value=True)),
        patch("callobuzz_rag.fairuse.gate.enforce_question_cap", AsyncMock()),
        patch(
            "callobuzz_rag.core.config_resolver.ConfigResolver._decrypt_corpus_key",
            return_value="sk-theirs",
        ),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            res = await c.post(
                "/api/v1/query",
                headers=ADMIN,
                json={"query": "q", "corpus_id": str(corpus.id)},
            )
    assert res.status_code == 200, res.text
    assert record.await_args.kwargs["payer"] == "byok"
    assert record.await_args.kwargs["model"] == "openai/gpt-oss-120b"


async def test_a_stream_that_fails_midway_still_meters_what_was_spent(app, corpus) -> None:  # noqa: F811
    orch, _chunk, _doc = _orchestrator(["partial"], fail=True)
    ledger = CostLedger()
    ledger.add(CallCost("openai/gpt-oss-20b", 40, 8, 0, 0.00004))
    orch.llm.cost_ledger = ledger
    recorded: list[dict] = []

    async def fake_record(_factory, **kwargs):
        recorded.append(kwargs)

    with (
        patch("callobuzz_rag.api.routes.query._build_orchestrator", return_value=orch),
        patch("callobuzz_rag.api.routes.query.record_usage_new_session", fake_record),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            res = await c.post(
                "/api/v1/query/stream",
                headers=ADMIN,
                json={"query": "q", "corpus_id": str(corpus.id)},
            )
    assert "error" in dict(_events(res.text))
    assert len(recorded) == 1
    assert recorded[0]["requests"] == 0  # not an answered question: spend only
    assert recorded[0]["cost"].cost_micro == 40
    assert recorded[0]["payer"] == "operator"
