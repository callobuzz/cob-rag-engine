"""POST /api/v1/query/stream speaks the widget's named-event SSE format.

It used to send unnamed `data: "<token>"` lines and `data: [DONE]`: no
citations, no query_id, nothing to attach feedback to.
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.api.app import create_app
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import RetrievalError
from callobuzz_rag.retrieval.models import ChunkScoreBreakdown

ADMIN = {"X-API-Key": "test-admin-key"}


def _events(text: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.fixture
def corpus() -> SimpleNamespace:
    cid = uuid.uuid4()
    return SimpleNamespace(
        id=cid,
        tenant_id=uuid.uuid4(),
        qdrant_collection=f"corpus_{str(cid)[:8]}",
        retrieval_config=None,
        llm_config=None,
        prompt_config=None,
    )


@pytest.fixture
def log_session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def app(corpus, log_session):
    application = create_app()
    application.state.settings = Settings(
        _env_file=None, admin_api_key="test-admin-key", llm_api_key="test"
    )

    @asynccontextmanager
    async def factory():
        yield log_session

    application.state.session_factory = factory

    from callobuzz_rag.api.deps import get_session

    request_session = AsyncMock()
    request_session.get = AsyncMock(return_value=corpus)

    async def override():
        yield request_session

    application.dependency_overrides[get_session] = override
    return application


def _orchestrator(
    tokens: list[str],
    *,
    fail: bool = False,
    retrieval_error: bool = False,
    steps: tuple[str, ...] = ("query_understanding", "embed", "search_dense", "fuse"),
):
    chunk_id = uuid.uuid4()
    document_id = uuid.uuid4()
    cited = [
        SimpleNamespace(
            citation_index=1,
            chunk_id=chunk_id,
            document_id=document_id,
            external_id="faq-7",
            file_name="returns.pdf",
            section="Returns > Time limits",
            page=2,
            text="Return within 30 days",
            score=0.91,
        ),
    ]
    messages = [
        SimpleNamespace(role="system", content="sys"),
        SimpleNamespace(role="user", content="ctx + question"),
    ]
    pipeline = SimpleNamespace(
        assembled=SimpleNamespace(messages=messages, cited_chunks=cited),
        processed_query="Can I return this?",
        score_breakdown={
            str(chunk_id): ChunkScoreBreakdown(
                fused_score=0.91, pipeline_rank=1, dense_rank=1, dense_score=0.82
            )
        },
        stage_timings_ms={name: 10 for name in steps},
    )

    async def run_pipeline(*args, on_stage=None, **kwargs):
        # The real pipeline tells the caller as each timed stage starts.
        for name in steps:
            if on_stage is not None:
                on_stage(name)
            await asyncio.sleep(0)
        return pipeline

    async def stream(msgs):
        for token in tokens:
            yield token
        if fail:
            raise RuntimeError("provider down: sk-secret")

    orch = MagicMock()
    if retrieval_error:
        orch.run_retrieval_pipeline = AsyncMock(side_effect=RetrievalError("qdrant down"))
    else:
        orch.run_retrieval_pipeline = AsyncMock(side_effect=run_pipeline)
    orch.llm.complete_stream = MagicMock(side_effect=stream)
    return orch, chunk_id, document_id


async def _stream(app, corpus, orch, query="Can I return this?"):
    recorded: list[dict[str, Any]] = []

    async def fake_record(session_factory, **kwargs):
        recorded.append(kwargs)

    with (
        patch("callobuzz_rag.api.routes.query._build_orchestrator", return_value=orch),
        patch("callobuzz_rag.api.routes.query.record_usage_new_session", fake_record),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            res = await c.post(
                "/api/v1/query/stream",
                headers=ADMIN,
                json={"query": query, "corpus_id": str(corpus.id)},
            )
    return res, recorded


async def test_sources_then_tokens_then_done(app, corpus, log_session) -> None:
    orch, chunk_id, document_id = _orchestrator(["Yes, ", "within 30 days."])

    res, recorded = await _stream(app, corpus, orch)

    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/event-stream")
    events = _events(res.text)
    names = [e[0] for e in events]
    # Progress first (understand -> search), then the answer, then the write stage's tokens.
    assert names == ["stage", "stage", "sources", "stage", "token", "token", "done"]
    assert [e[1]["stage"] for e in events if e[0] == "stage"] == [
        "understand",
        "search",
        "write",
    ]
    assert all(isinstance(e[1]["elapsed_ms"], int) for e in events if e[0] == "stage")
    events = [e for e in events if e[0] != "stage"]

    sources = events[0][1]
    query_id = sources["query_id"]
    breakdown = sources["sources"][0].pop("score_breakdown")
    assert breakdown["dense_score"] == 0.82 and breakdown["pipeline_rank"] == 1
    assert sources["sources"] == [
        {
            "index": 1,
            "chunk_id": str(chunk_id),
            "document_id": str(document_id),
            "external_id": "faq-7",
            "file_name": "returns.pdf",
            "section": "Returns > Time limits",
            "page": 2,
            "snippet": "Return within 30 days",
            "score": 0.91,
        }
    ]
    assert "".join(e[1]["content"] for e in events if e[0] == "token") == "Yes, within 30 days."

    done = events[-1][1]
    assert done["query_id"] == query_id
    assert done["model"]
    assert done["usage"]["completion_tokens"] > 0
    assert done["usage"]["total_tokens"] == (
        done["usage"]["prompt_tokens"] + done["usage"]["completion_tokens"]
    )
    # Timings per public stage; the engine's finer steps ride along for the playground.
    assert set(done["timings"]) == {"understand", "search", "write"}
    assert done["timings"]["understand"] == 10
    assert done["timings"]["search"] == 30  # embed + search_dense + fuse
    assert done["stage_timings_ms"]["embed"] == 10
    assert done["processed_query"] == "Can I return this?"
    assert done["retrieval_ms"] >= 0
    # A model that reported no thinking has none shown.
    assert "thinking_ms" not in done and "reasoning_tokens" not in done

    # The query_id is real: its query_log row was written (and committed).
    log = log_session.add.call_args.args[0]
    assert str(log.id) == query_id
    assert log.corpus_id == corpus.id
    assert log.response == "Yes, within 30 days."
    assert log.retrieved_chunk_ids == [chunk_id]
    assert log.confidence_score is None  # never validated on a stream
    log_session.commit.assert_awaited()

    assert len(recorded) == 1
    assert recorded[0]["source"] == "stream"
    assert recorded[0]["tenant_id"] == corpus.tenant_id
    assert recorded[0]["completion_tokens"] == done["usage"]["completion_tokens"]


async def test_generation_failure_is_an_error_event(app, corpus, log_session) -> None:
    orch, _, _ = _orchestrator(["partial"], fail=True)

    res, recorded = await _stream(app, corpus, orch)

    events = _events(res.text)
    assert [e[0] for e in events if e[0] != "stage"] == ["sources", "token", "error"]
    assert "sk-secret" not in res.text  # provider internals never reach the client
    assert recorded == []
    log_session.add.assert_not_called()


async def test_retrieval_failure_is_an_error_event(app, corpus) -> None:
    """The pipeline now runs inside the stream (so `stage` events are live): its failure
    arrives as an `error` event with a code, after the first `stage`, and nothing is generated."""
    orch, _, _ = _orchestrator([], retrieval_error=True)

    res, recorded = await _stream(app, corpus, orch)

    assert res.status_code == 200
    events = _events(res.text)
    assert [e[0] for e in events] == ["stage", "error"]
    assert events[1][1]["code"] == "retrieval_failed"
    assert events[1][1]["detail"] == "qdrant down"
    orch.llm.complete_stream.assert_not_called()
    assert recorded == []


async def test_reranking_is_a_stage_only_when_it_ran(app, corpus) -> None:
    orch, _, _ = _orchestrator(
        ["ok"], steps=("query_understanding", "embed", "search_dense", "rerank", "assemble")
    )

    res, _ = await _stream(app, corpus, orch)

    events = _events(res.text)
    assert [e[1]["stage"] for e in events if e[0] == "stage"] == [
        "understand",
        "search",
        "rerank",
        "write",
    ]
    done = events[-1][1]
    assert done["timings"]["rerank"] == 10
    assert done["timings"]["search"] == 30  # embed + search_dense + assemble: all searching


async def test_reasoning_model_reports_thinking_time_and_tokens(app, corpus) -> None:
    """A provider that sees reasoning records it on the installed StreamStats; the stream
    reports it, and never invents it for a model that did not think."""
    import time

    from callobuzz_rag.llm.stream_stats import current_stream_stats

    orch, _, _ = _orchestrator([])

    async def thinking_stream(msgs):
        stats = current_stream_stats()
        assert stats is not None
        stats.first_reasoning_at = time.monotonic()
        await asyncio.sleep(0.02)
        stats.first_content_at = time.monotonic()
        stats.reasoning_tokens = 321
        yield "Answer."

    orch.llm.complete_stream = MagicMock(side_effect=thinking_stream)

    res, _ = await _stream(app, corpus, orch)

    done = _events(res.text)[-1][1]
    assert done["thinking_ms"] >= 15
    assert done["reasoning_tokens"] == 321
    assert done["usage"]["reasoning_tokens"] == 321


async def test_client_hanging_up_on_done_loses_no_write(app, corpus, log_session) -> None:
    """A client that closes the connection on `done` (SSE clients do) used to cut
    the usage write off mid-query; the broken connection then failed the next
    request with "connection is closed". Every write now precedes `done`."""
    import asyncio

    from tests.unit.asgi_client import call

    orch, _, _ = _orchestrator(["Yes."])
    writes: list[str] = []

    async def slow_record(session_factory, **kwargs):
        try:
            await asyncio.sleep(0.02)  # a real INSERT takes a moment
            writes.append("usage")
        except asyncio.CancelledError:
            writes.append("usage:cancelled")
            raise

    with (
        patch("callobuzz_rag.api.routes.query._build_orchestrator", return_value=orch),
        patch("callobuzz_rag.api.routes.query.record_usage_new_session", slow_record),
    ):
        status, body = await call(
            app,
            "POST",
            "/api/v1/query/stream",
            headers=ADMIN,
            json_body={"query": "Can I return this?", "corpus_id": str(corpus.id)},
            hang_up_after=b"event: done",
        )
        await asyncio.sleep(0.1)

    assert status == 200 and b"event: done" in body
    assert writes == ["usage"]
    log_session.commit.assert_awaited()


async def test_hanging_up_mid_search_ends_the_search_before_the_session_closes(app, corpus) -> None:
    """The visitor leaves while retrieval is still running: the search task is cancelled and
    finishes (it may be mid-query on the request's session) BEFORE that session is closed."""
    from callobuzz_rag.api.deps import get_session
    from tests.unit.asgi_client import call

    order: list[str] = []
    request_session = AsyncMock()
    request_session.get = AsyncMock(return_value=corpus)

    async def close() -> None:
        order.append("session_closed")

    request_session.close = close

    async def override():
        yield request_session

    app.dependency_overrides[get_session] = override

    async def slow_pipeline(*args, on_stage=None, **kwargs):
        if on_stage is not None:
            on_stage("embed")
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            order.append("search_cancelled")
            await asyncio.sleep(0.05)  # unwinding its in-flight query takes a moment
            order.append("search_finished")
            raise

    orch, _, _ = _orchestrator(["Yes."])
    orch.run_retrieval_pipeline = AsyncMock(side_effect=slow_pipeline)

    with patch("callobuzz_rag.api.routes.query._build_orchestrator", return_value=orch):
        await call(
            app,
            "POST",
            "/api/v1/query/stream",
            headers=ADMIN,
            json_body={"query": "Can I return this?", "corpus_id": str(corpus.id)},
            hang_up_after=b"event: stage",
        )
        await asyncio.sleep(0.3)

    assert order == ["search_cancelled", "search_finished", "session_closed"]


GEMMA = "google/gemma-4-31b-it"
HINDI = "मुझे रिटर्न पॉलिसी बताओ"


def _routed(orch, tokens: list[str]) -> MagicMock:
    async def stream(msgs):
        for token in tokens:
            yield token

    routed = MagicMock()
    routed.complete_stream = MagicMock(side_effect=stream)
    orch.llm.for_model = MagicMock(return_value=routed)
    return routed


async def test_a_hindi_stream_is_written_by_the_routed_model_and_metered_under_it(
    app, corpus
) -> None:
    corpus.llm_config = {"language_models": {"hi": GEMMA}}
    orch, _, _ = _orchestrator(["engine"])
    routed = _routed(orch, ["namaste"])

    res, recorded = await _stream(app, corpus, orch, HINDI)

    events = _events(res.text)
    assert "".join(e[1]["content"] for e in events if e[0] == "token") == "namaste"
    assert events[-1][1]["model"] == GEMMA
    orch.llm.for_model.assert_called_once_with(GEMMA, "off")
    routed.complete_stream.assert_called_once()
    orch.llm.complete_stream.assert_not_called()
    assert [r["model"] for r in recorded] == [GEMMA]


async def test_an_english_stream_keeps_the_engine_model(app, corpus) -> None:
    corpus.llm_config = {"language_models": {"hi": GEMMA}}
    orch, _, _ = _orchestrator(["Yes."])
    _routed(orch, ["never"])

    res, recorded = await _stream(app, corpus, orch)

    assert events_model(res) != GEMMA
    orch.llm.for_model.assert_not_called()
    assert [r["model"] for r in recorded] != [GEMMA]


def events_model(res) -> str:
    return _events(res.text)[-1][1]["model"]


async def test_the_query_endpoint_answers_hindi_on_the_routed_model(app, corpus) -> None:
    """POST /query (assistant off) hands the orchestrator the routed provider for the answer
    and meters the model the answer reports."""
    corpus.llm_config = {"language_models": {"hi": GEMMA}}
    orch = MagicMock()
    routed = MagicMock(name="routed")
    orch.llm.for_model = MagicMock(return_value=routed)
    orch.query = AsyncMock(
        return_value={
            "answer": "namaste",
            "citations": [],
            "confidence": 1.0,
            "query_id": str(uuid.uuid4()),
            "model_used": GEMMA,
            "latency_ms": 5,
            "token_usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
    )
    recorded: list[dict[str, Any]] = []

    async def fake_record(session, **kwargs):
        recorded.append(kwargs)

    with (
        patch("callobuzz_rag.api.routes.query._build_orchestrator", return_value=orch),
        patch("callobuzz_rag.api.routes.query.record_usage", fake_record),
        patch("callobuzz_rag.api.routes.query.request_cost_of", return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            res = await c.post(
                "/api/v1/query",
                headers=ADMIN,
                json={"query": HINDI, "corpus_id": str(corpus.id)},
            )
            english = await c.post(
                "/api/v1/query",
                headers=ADMIN,
                json={"query": "Can I return this?", "corpus_id": str(corpus.id)},
            )

    assert res.status_code == 200, res.text
    assert english.status_code == 200, english.text
    kwargs = [call.kwargs for call in orch.query.await_args_list]
    assert kwargs[0]["answer_llm"] is routed
    assert kwargs[1]["answer_llm"] is orch.llm
    assert recorded[0]["model"] == GEMMA
    orch.llm.for_model.assert_called_once_with(GEMMA, "off")
