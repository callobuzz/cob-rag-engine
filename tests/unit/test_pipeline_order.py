"""The reranker's ordering has to survive to the caller.

Every post-fusion stage reorders `fused`; none of them wrote its score back onto
`SearchResult.score`, which stayed the RRF fusion score. `ContextAssembler`
then re-sorted by `score` and restored fusion order, so the most expensive stage
in the system chose WHICH chunks survived and had its ORDERING thrown away.

Measured on a production corpus before the fix, query "packing procedure",
`final_k: 5` — delivered `score` equalled `fused_score` to the last digit for
every chunk, and the chunk the cross-encoder scored highest (0.3793) came back
fourth:

    pos     score     fused p_rank  rerank
      1   0.03202   0.03202      3  0.2417
      4   0.01587   0.01587      1  0.3793   <- best rerank score, delivered 4th

`pipeline_order_enabled` is per-corpus and defaults OFF, because it changes both
the ordering and the `score` NUMBER every caller sees.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.retrieval.context_assembly import AssembledContext
from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator, _write_scores_back
from callobuzz_rag.retrieval.reranking.base import RerankResult
from callobuzz_rag.vectordb.base import SearchResult

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
C = "33333333-3333-4333-8333-333333333333"


def _settings(**overrides: object) -> MagicMock:
    # Every toggle explicit: an unlisted attribute comes back from MagicMock as a
    # truthy child mock and silently runs a branch the test never asked for.
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
    reranked: list[RerankResult] | None = None,
    chunk_ages: dict[str, float] | None = None,
    **setting_overrides: object,
) -> tuple[QueryOrchestrator, AsyncMock]:
    """Returns the orchestrator and the assembler mock, so the call can be read."""
    embedder = AsyncMock()
    embedding = MagicMock()
    embedding.vector = [0.1] * 8
    embedder.embed_texts = AsyncMock(return_value=[embedding])

    vector_store = AsyncMock()
    vector_store.search = AsyncMock(return_value=dense)
    vector_store.search_sparse = AsyncMock(return_value=[])

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
    return orch, assembler


def _dense(*ids: str) -> list[SearchResult]:
    return [SearchResult(id=cid, score=1.0 - i * 0.1, payload={}) for i, cid in enumerate(ids)]


def _inverts_dense_order() -> list[RerankResult]:
    """The reranker disagrees with dense search completely: C, B, A."""
    return [
        RerankResult(index=2, score=0.9, text="text for C"),
        RerankResult(index=1, score=0.5, text="text for B"),
        RerankResult(index=0, score=0.1, text="text for A"),
    ]


class TestDefaultIsUnchanged:
    """OFF must reproduce the historical behaviour exactly — it is the A in the A/B."""

    @pytest.mark.asyncio
    async def test_score_stays_the_fusion_score(self) -> None:
        orch, _ = _build(_dense(A, B, C), reranked=_inverts_dense_order())

        result = await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

        for r in result.top_results:
            assert r.score == pytest.approx(result.score_breakdown[r.id].fused_score)

    @pytest.mark.asyncio
    async def test_assembly_still_re_sorts(self) -> None:
        orch, assembler = _build(_dense(A, B, C), reranked=_inverts_dense_order())

        await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

        assert assembler.assemble.await_args.kwargs["preserve_order"] is False


class TestContextBudgetFlag:
    @pytest.mark.asyncio
    async def test_budget_applies_by_default_and_can_be_lifted(self) -> None:
        orch, assembler = _build(_dense(A, B, C), reranked=_inverts_dense_order())

        await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())
        assert assembler.assemble.await_args.kwargs["token_budget"] is True

        await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4(), context_budget=False)
        assert assembler.assemble.await_args.kwargs["token_budget"] is False


class TestPipelineOrderEnabled:
    @pytest.mark.asyncio
    async def test_rerank_score_is_written_onto_score(self) -> None:
        orch, _ = _build(
            _dense(A, B, C),
            reranked=_inverts_dense_order(),
            pipeline_order_enabled=True,
        )

        result = await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

        by_id = {r.id: r.score for r in result.top_results}
        assert by_id[C] == pytest.approx(0.9)
        assert by_id[B] == pytest.approx(0.5)
        assert by_id[A] == pytest.approx(0.1)

    @pytest.mark.asyncio
    async def test_the_rerankers_order_is_the_delivered_order(self) -> None:
        """The whole point: dense said A, B, C and the cross-encoder said C, B, A."""
        orch, _ = _build(
            _dense(A, B, C),
            reranked=_inverts_dense_order(),
            pipeline_order_enabled=True,
        )

        result = await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

        assert [r.id for r in result.top_results] == [C, B, A]

    @pytest.mark.asyncio
    async def test_assembly_is_told_to_trust_the_order(self) -> None:
        orch, assembler = _build(
            _dense(A, B, C),
            reranked=_inverts_dense_order(),
            pipeline_order_enabled=True,
        )

        await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

        assert assembler.assemble.await_args.kwargs["preserve_order"] is True

    @pytest.mark.asyncio
    async def test_fused_score_is_still_reported_for_comparison(self) -> None:
        """`score` changes meaning, so the raw fusion score has to stay reachable."""
        orch, _ = _build(
            _dense(A, B, C),
            reranked=_inverts_dense_order(),
            pipeline_order_enabled=True,
        )

        result = await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

        assert result.score_breakdown[A].fused_score == pytest.approx(1 / 61)
        assert result.score_breakdown[A].rerank_score == pytest.approx(0.1)

    @pytest.mark.asyncio
    async def test_decay_writes_the_decayed_score(self) -> None:
        orch, _ = _build(
            _dense(A, B, C),
            chunk_ages={A: 365.0, B: 0.0, C: 0.0},
            temporal_decay_enabled=True,
            pipeline_order_enabled=True,
        )

        result = await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

        by_id = {r.id: r.score for r in result.top_results}
        breakdown = result.score_breakdown
        # A is a year old: its delivered score must be its fused score scaled by
        # the multiplier, not the fused score itself.
        assert breakdown[A].decay_multiplier is not None
        assert by_id[A] == pytest.approx(
            breakdown[A].fused_score * breakdown[A].decay_multiplier  # type: ignore[operator]
        )
        assert by_id[A] < breakdown[A].fused_score


class TestWriteScoresBack:
    def test_leaves_unscored_results_untouched(self) -> None:
        """The reranker returns only its top `rerank_top_k_output`.

        Zeroing the rest would reorder chunks the stage never had an opinion
        about — worse than the bug being fixed.
        """
        results = _dense(A, B, C)

        out = _write_scores_back(results, {A: 0.42})

        assert [r.score for r in out] == [0.42, results[1].score, results[2].score]

    def test_empty_score_map_is_a_no_op(self) -> None:
        results = _dense(A, B)

        assert _write_scores_back(results, {}) == results

    def test_does_not_reorder(self) -> None:
        """Write-back carries the number; ordering stays each stage's own business."""
        results = _dense(A, B, C)

        out = _write_scores_back(results, {A: 0.1, B: 0.9, C: 0.5})

        assert [r.id for r in out] == [A, B, C]
