"""Unit tests for QueryOrchestrator."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.core.exceptions import QueryError, RetrievalError
from callobuzz_rag.llm.models import LLMMessage, LLMResponse, LLMUsage
from callobuzz_rag.retrieval.context_assembly import AssembledContext, CitedChunk
from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator
from callobuzz_rag.vectordb.base import SearchResult


def _mock_settings(**overrides: object) -> MagicMock:
    defaults = {
        "query_top_k": 20,
        "query_final_k": 5,
        "query_classification_enabled": False,
        "hyde_enabled": False,
        "rrf_k": 60,
        "qdrant_collection": "chunks",
        "validation_enabled": False,
        "mmr_enabled": False,
        "temporal_decay_enabled": False,
        # Must be listed explicitly. Every flag NOT named here comes back from
        # MagicMock as a truthy child mock, so an unlisted toggle silently runs
        # its branch in tests that never asked for it — which is how adding the
        # BM25 leg turned "All search attempts failed" into "No results after
        # fusion" in a test that has nothing to do with hybrid search.
        "hybrid_search_enabled": False,
        "pipeline_order_enabled": False,
        "retrieval_top_k_sparse": 20,
        "rerank_top_k_output": 10,
        "mmr_lambda": 0.7,
        "temporal_decay_half_life_days": 30.0,
        "validation_confidence_threshold": 0.7,
        "validation_gatekeeper_weight": 0.4,
        "validation_auditor_weight": 0.4,
        "validation_strategist_weight": 0.2,
        "multi_query_count": 3,
        "query_stream_enabled": True,
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def _mock_embedding(vector: list[float] | None = None) -> MagicMock:
    emb = MagicMock()
    emb.vector = vector or [0.1] * 10
    return emb


def _mock_llm_response(content: str = "Answer") -> LLMResponse:
    return LLMResponse(
        content=content,
        model="test-model",
        usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _mock_assembled(
    answer_text: str = "chunk text",
) -> AssembledContext:
    chunk_id = uuid.uuid4()
    return AssembledContext(
        messages=[
            LLMMessage(role="system", content="system"),
            LLMMessage(role="user", content="user"),
        ],
        cited_chunks=[
            CitedChunk(
                citation_index=1,
                chunk_id=chunk_id,
                text=answer_text,
                file_name="doc.pdf",
                page=1,
                score=0.9,
                token_count=10,
            )
        ],
        total_context_tokens=10,
        chunks_included=1,
        chunks_dropped=0,
    )


def _search_results(n: int = 3) -> list[SearchResult]:
    return [SearchResult(id=str(uuid.uuid4()), score=1.0 - i * 0.1, payload={}) for i in range(n)]


@pytest.fixture
def orchestrator() -> QueryOrchestrator:
    session = AsyncMock()
    settings = _mock_settings()
    embedder = AsyncMock()
    embedder.embed_texts = AsyncMock(return_value=[_mock_embedding()])
    vector_store = AsyncMock()
    vector_store.search = AsyncMock(return_value=_search_results())
    llm_provider = AsyncMock()
    llm_provider.complete = AsyncMock(return_value=_mock_llm_response())
    context_assembler = AsyncMock()
    context_assembler.assemble = AsyncMock(return_value=_mock_assembled())

    return QueryOrchestrator(
        session=session,
        settings=settings,
        embedder=embedder,
        vector_store=vector_store,
        llm_provider=llm_provider,
        context_assembler=context_assembler,
    )


class TestQueryOrchestrator:
    @pytest.mark.asyncio
    async def test_query_happy_path(self, orchestrator: QueryOrchestrator) -> None:
        result = await orchestrator.query(
            text="What is X?",
            corpus_id=uuid.uuid4(),
        )
        assert "answer" in result
        assert result["answer"] == "Answer"
        assert "citations" in result
        assert "confidence" in result
        assert "query_id" in result
        assert "model_used" in result
        assert "latency_ms" in result

    @pytest.mark.asyncio
    async def test_token_usage_has_no_reasoning_key_without_thinking(
        self, orchestrator: QueryOrchestrator
    ) -> None:
        result = await orchestrator.query(text="What is X?", corpus_id=uuid.uuid4())
        assert result["token_usage"] == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }

    @pytest.mark.asyncio
    async def test_token_usage_reports_reasoning_tokens(
        self, orchestrator: QueryOrchestrator
    ) -> None:
        orchestrator._llm.complete = AsyncMock(  # type: ignore[attr-defined]
            return_value=LLMResponse(
                content="Answer",
                model="m",
                usage=LLMUsage(
                    prompt_tokens=10, completion_tokens=300, total_tokens=310, reasoning_tokens=240
                ),
            )
        )
        result = await orchestrator.query(text="What is X?", corpus_id=uuid.uuid4())
        assert result["token_usage"]["reasoning_tokens"] == 240
        assert result["token_usage"]["completion_tokens"] == 300

    @pytest.mark.asyncio
    async def test_query_empty_text_raises(self, orchestrator: QueryOrchestrator) -> None:
        with pytest.raises(QueryError, match="Empty query"):
            await orchestrator.query(text="   ", corpus_id=uuid.uuid4())

    @pytest.mark.asyncio
    async def test_query_embedding_failure_raises_retrieval_error(self) -> None:
        session = AsyncMock()
        embedder = AsyncMock()
        embedder.embed_texts = AsyncMock(side_effect=RuntimeError("API down"))
        orch = QueryOrchestrator(
            session=session,
            settings=_mock_settings(),
            embedder=embedder,
            vector_store=AsyncMock(),
            llm_provider=AsyncMock(),
            context_assembler=AsyncMock(),
        )
        with pytest.raises(RetrievalError, match="Embedding failed"):
            await orch.query(text="test", corpus_id=uuid.uuid4())

    @pytest.mark.asyncio
    async def test_query_search_all_fail_raises(self) -> None:
        session = AsyncMock()
        embedder = AsyncMock()
        embedder.embed_texts = AsyncMock(return_value=[_mock_embedding()])
        vector_store = AsyncMock()
        vector_store.search = AsyncMock(side_effect=RuntimeError("Qdrant down"))
        orch = QueryOrchestrator(
            session=session,
            settings=_mock_settings(),
            embedder=embedder,
            vector_store=vector_store,
            llm_provider=AsyncMock(),
            context_assembler=AsyncMock(),
        )
        with pytest.raises(RetrievalError, match="All search attempts failed"):
            await orch.query(text="test", corpus_id=uuid.uuid4())

    @pytest.mark.asyncio
    async def test_query_fusion_empty_returns_no_citations(
        self, orchestrator: QueryOrchestrator
    ) -> None:
        """A corpus with no matching chunks is a legitimate empty result, not an
        infrastructure failure — the caller must get a 200 so it can apply its
        own "insufficient evidence" handling instead of treating an empty
        corpus as the engine being down. `assemble` is mocked to a fixed
        return regardless of input, so the real assertion is on what it was
        CALLED with: an empty result list, proving fusion's empty output flows
        through unmolested rather than raising."""
        orchestrator._vector_store.search = AsyncMock(return_value=[])  # empty results
        result = await orchestrator.query(text="test", corpus_id=uuid.uuid4())
        assert "answer" in result
        assembler_mock = orchestrator._assembler.assemble
        assert assembler_mock.call_args.args[1] == []

    @pytest.mark.asyncio
    async def test_query_llm_failure_raises_query_error(
        self, orchestrator: QueryOrchestrator
    ) -> None:
        orchestrator._llm.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
        with pytest.raises(QueryError, match="LLM generation failed"):
            await orchestrator.query(text="test", corpus_id=uuid.uuid4())

    @pytest.mark.asyncio
    async def test_query_validation_disabled(self, orchestrator: QueryOrchestrator) -> None:
        result = await orchestrator.query(
            text="test", corpus_id=uuid.uuid4(), enable_validation=False
        )
        assert result["confidence"] == 1.0

    @pytest.mark.asyncio
    async def test_query_validation_enabled(self) -> None:
        session = AsyncMock()
        embedder = AsyncMock()
        embedder.embed_texts = AsyncMock(return_value=[_mock_embedding()])
        vector_store = AsyncMock()
        vector_store.search = AsyncMock(return_value=_search_results())
        llm_provider = AsyncMock()
        llm_provider.complete = AsyncMock(return_value=_mock_llm_response())
        assembler = AsyncMock()
        assembler.assemble = AsyncMock(return_value=_mock_assembled())

        settings = _mock_settings(validation_enabled=True)
        orch = QueryOrchestrator(
            session=session,
            settings=settings,
            embedder=embedder,
            vector_store=vector_store,
            llm_provider=llm_provider,
            context_assembler=assembler,
        )

        with patch.object(orch, "_run_validation", new_callable=AsyncMock) as mock_val:
            mock_val.return_value = 0.85
            result = await orch.query(text="test", corpus_id=uuid.uuid4(), enable_validation=True)
            assert result["confidence"] == 0.85
            mock_val.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_query_flush(self, orchestrator: QueryOrchestrator) -> None:
        await orchestrator.query(text="test", corpus_id=uuid.uuid4())
        orchestrator._session.flush.assert_called()
        orchestrator._session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_log_query_error_no_crash(self) -> None:
        session = AsyncMock()
        session.flush = AsyncMock(side_effect=RuntimeError("DB write failed"))
        embedder = AsyncMock()
        embedder.embed_texts = AsyncMock(return_value=[_mock_embedding()])
        vector_store = AsyncMock()
        vector_store.search = AsyncMock(return_value=_search_results())
        llm_provider = AsyncMock()
        llm_provider.complete = AsyncMock(return_value=_mock_llm_response())
        assembler = AsyncMock()
        assembler.assemble = AsyncMock(return_value=_mock_assembled())

        orch = QueryOrchestrator(
            session=session,
            settings=_mock_settings(),
            embedder=embedder,
            vector_store=vector_store,
            llm_provider=llm_provider,
            context_assembler=assembler,
        )
        # Should not raise — _log_query catches DB errors
        result = await orch.query(text="test", corpus_id=uuid.uuid4())
        assert result["answer"] == "Answer"
