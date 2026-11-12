"""The per-stage score breakdown returned alongside each chunk.

The pipeline hands the caller one number per chunk — the RRF fusion score — and
every stage after fusion (rerank, MMR, decay) only REORDERS; none of them writes
its score back. So `score` alone cannot answer "did BM25 find this?", "did the
cross-encoder rescue it?", or "is the reranker earning its latency?". These tests
pin the breakdown that answers those, including the one uncomfortable fact it
exposes: `pipeline_rank` is not the order the caller receives.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.retrieval.context_assembly import AssembledContext
from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator
from callobuzz_rag.retrieval.reranking.base import RerankResult
from callobuzz_rag.vectordb.base import SearchResult

# Fixed ids so assertions can name a chunk rather than an index.
A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
C = "33333333-3333-4333-8333-333333333333"


def _settings(**overrides: object) -> MagicMock:
    # Every toggle listed explicitly: an unnamed attribute comes back from
    # MagicMock as a truthy child mock and silently runs a branch the test never
    # asked for.
    defaults: dict[str, object] = {
        "query_top_k": 20,
        "query_final_k": 3,
        "query_classification_enabled": False,
        "hyde_enabled": False,
        "rrf_k": 60,
        "qdrant_collection": "chunks",
        "mmr_enabled": False,
        "temporal_decay_enabled": False,
        "hybrid_search_enabled": False,
        "pipeline_order_enabled": False,
        "retrieval_top_k_sparse": 20,
        "rerank_top_k_output": 10,
        "rerank_top_n_input": 50,
        "mmr_lambda": 0.7,
        "temporal_decay_half_life_days": 30.0,
        "multi_query_count": 3,
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def _build(
    dense: list[SearchResult],
    *,
    sparse: list[SearchResult] | None = None,
    reranked: list[RerankResult] | None = None,
    chunk_ages: dict[str, float] | None = None,
    **setting_overrides: object,
) -> QueryOrchestrator:
    """Wire an orchestrator whose only real moving part is the ranking pipeline."""
    embedder = AsyncMock()
    embedding = MagicMock()
    embedding.vector = [0.1] * 8
    embedder.embed_texts = AsyncMock(return_value=[embedding])

    vector_store = AsyncMock()
    vector_store.search = AsyncMock(return_value=dense)
    vector_store.search_sparse = AsyncMock(return_value=sparse or [])

    reranker = None
    if reranked is not None:
        reranker = AsyncMock()
        reranker.rerank = AsyncMock(return_value=reranked)

    assembler = AsyncMock()
    assembler.assemble = AsyncMock(
        return_value=AssembledContext(
            messages=[],
            cited_chunks=[],
            total_context_tokens=0,
            chunks_included=0,
            chunks_dropped=0,
        )
    )

    orch = QueryOrchestrator(
        session=AsyncMock(),
        settings=_settings(**setting_overrides),
        embedder=embedder,
        vector_store=vector_store,
        llm_provider=AsyncMock(),
        context_assembler=assembler,
        reranker=reranker,
    )

    now = datetime.now(UTC)
    ages = chunk_ages or {}
    orch._load_chunk_data = AsyncMock(  # type: ignore[method-assign]
        return_value={
            cid: (f"text for {cid}", now - timedelta(days=ages.get(cid, 0.0))) for cid in (A, B, C)
        }
    )
    return orch


def _dense(*ids: str, categories: dict[str, str] | None = None) -> list[SearchResult]:
    cats = categories or {}
    return [
        SearchResult(
            id=cid,
            score=1.0 - i * 0.1,
            payload={"category": cats[cid]} if cid in cats else {},
        )
        for i, cid in enumerate(ids)
    ]


@pytest.mark.asyncio
async def test_dense_rank_and_fused_score_are_reported() -> None:
    orch = _build(_dense(A, B, C))

    result = await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

    assert set(result.score_breakdown) == {A, B, C}
    assert [result.score_breakdown[c].dense_rank for c in (A, B, C)] == [1, 2, 3]
    assert [result.score_breakdown[c].pipeline_rank for c in (A, B, C)] == [1, 2, 3]
    # RRF: 1/(k + rank) with k=60.
    assert result.score_breakdown[A].fused_score == pytest.approx(1 / 61)
    assert result.score_breakdown[C].fused_score == pytest.approx(1 / 63)


@pytest.mark.asyncio
async def test_stages_that_did_not_run_report_none() -> None:
    """A null is an answer, not a gap — it says the stage was off."""
    orch = _build(_dense(A, B))

    result = await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

    breakdown = result.score_breakdown[A]
    assert breakdown.sparse_rank is None
    assert breakdown.rerank_score is None
    assert breakdown.decay_multiplier is None
    assert breakdown.age_days is None


@pytest.mark.asyncio
async def test_bm25_only_hit_has_sparse_rank_and_no_dense_rank() -> None:
    """The case hybrid search exists for: an identifier dense search never saw."""
    orch = _build(
        _dense(A, B),
        sparse=[
            SearchResult(id=C, score=9.0, payload={}),
            SearchResult(id=A, score=4.0, payload={}),
        ],
        hybrid_search_enabled=True,
    )

    result = await orch.run_retrieval_pipeline(text="ROLLER-10", corpus_id=uuid.uuid4())

    assert result.score_breakdown[C].dense_rank is None
    assert result.score_breakdown[C].sparse_rank == 1
    # A was found by both legs, which is exactly what RRF rewards.
    assert result.score_breakdown[A].dense_rank == 1
    assert result.score_breakdown[A].sparse_rank == 2
    # Raw per-leg scores ride along with the ranks.
    assert result.score_breakdown[A].dense_score == pytest.approx(1.0)
    assert result.score_breakdown[A].sparse_score == pytest.approx(4.0)
    assert result.score_breakdown[C].dense_score is None
    assert result.score_breakdown[C].sparse_score == pytest.approx(9.0)


@pytest.mark.asyncio
async def test_keyword_leg_uses_the_ingest_analyzer_and_the_dense_width() -> None:
    """Query terms go through the same analyzer as chunks, and the keyword leg
    returns as many candidates as the dense leg was asked for."""
    from callobuzz_rag.retrieval.sparse import query_to_sparse_vector

    orch = _build(_dense(A), hybrid_search_enabled=True)

    await orch.run_retrieval_pipeline(
        text="Which brands ship ROLLER-10?", corpus_id=uuid.uuid4(), top_k=100
    )

    args = orch._vector_store.search_sparse.await_args.args
    expected = query_to_sparse_vector("Which brands ship ROLLER-10?")
    assert (args[1].indices, args[1].values) == (expected.indices, expected.values)
    assert args[2] == 100  # not the 20-candidate keyword default


@pytest.mark.asyncio
async def test_keyword_mode_searches_bm25_alone_without_embedding() -> None:
    orch = _build(_dense(A, B), sparse=[SearchResult(id=C, score=7.0, payload={})])

    result = await orch.run_retrieval_pipeline(
        text="ROLLER-10", corpus_id=uuid.uuid4(), keyword_only=True
    )

    orch._vector_store.search.assert_not_called()
    orch._embedder.embed_texts.assert_not_called()
    assert [r.id for r in result.top_results] == [C]
    assert result.score_breakdown[C].dense_rank is None


@pytest.mark.asyncio
async def test_keyword_mode_with_only_stopwords_is_an_empty_answer() -> None:
    orch = _build(_dense(A))

    result = await orch.run_retrieval_pipeline(
        text="what is the", corpus_id=uuid.uuid4(), keyword_only=True
    )

    assert result.top_results == []
    orch._vector_store.search_sparse.assert_not_called()


@pytest.mark.asyncio
async def test_reranker_sees_only_the_top_n_fused_candidates() -> None:
    orch = _build(_dense(A, B, C), reranked=[], rerank_top_n_input=2)

    await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4(), enable_reranking=True)

    documents = orch._reranker.rerank.await_args.kwargs["documents"]
    assert len(documents) == 2


@pytest.mark.asyncio
async def test_rerank_score_is_reported_and_reveals_the_rescue() -> None:
    """The cross-encoder's opinion is otherwise invisible: it reorders, it does
    not rewrite `score`. Here it promotes the chunk fusion ranked last."""
    orch = _build(
        _dense(A, B, C),
        # Fused order is A, B, C; the cross-encoder inverts it.
        reranked=[
            RerankResult(index=2, score=0.95, text="text for C"),
            RerankResult(index=1, score=0.40, text="text for B"),
            RerankResult(index=0, score=0.05, text="text for A"),
        ],
    )

    result = await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

    assert result.score_breakdown[C].rerank_score == pytest.approx(0.95)
    assert result.score_breakdown[A].rerank_score == pytest.approx(0.05)
    # C is now first by the pipeline's reckoning...
    assert result.score_breakdown[C].pipeline_rank == 1
    assert result.score_breakdown[A].pipeline_rank == 3
    # ...while still carrying the HIGHEST fusion score, because rerank never
    # touched it. That gap is the whole reason this breakdown exists: anything
    # sorting on `score` downstream — including the engine's own context
    # assembler — silently undoes the rerank.
    assert result.score_breakdown[A].fused_score > result.score_breakdown[C].fused_score


@pytest.mark.asyncio
async def test_decay_multiplier_and_age_are_reported() -> None:
    orch = _build(
        _dense(A, B),
        chunk_ages={A: 30.0, B: 0.0},
        temporal_decay_enabled=True,
    )

    result = await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

    # One half-life old.
    assert result.score_breakdown[A].decay_multiplier == pytest.approx(0.5, abs=1e-3)
    assert result.score_breakdown[A].age_days == pytest.approx(30.0, abs=0.01)
    assert result.score_breakdown[B].decay_multiplier == pytest.approx(1.0, abs=1e-3)


@pytest.mark.asyncio
async def test_breakdown_covers_only_the_chunks_returned() -> None:
    """final_k trims; the breakdown must not leak candidates the caller never got."""
    orch = _build(_dense(A, B, C), query_final_k=2)

    result = await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

    assert set(result.score_breakdown) == {A, B}
    assert len(result.top_results) == 2
