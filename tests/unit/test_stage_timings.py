"""Where the ~16s of a retrieval actually goes.

The published breakdown — embedding and vector search a 0.76s floor, the CPU
cross-encoder about 0.5s per candidate above it, query understanding a further 5-9s —
was produced by **ablation**: one query re-run at six values of `top_k`, and again with
classification and HyDE toggled, subtracting to attribute the difference. There was no per-stage instrumentation in
`orchestrator.py`, which is why it had to be measured by ablation.

Ablation measures the shape of the cost across a controlled set of runs. It cannot tell
you where a single slow production query went, and it cannot be run against live traffic
at all. `_StageTimings` can.

What these tests pin is not the durations — those are wall clock and untestable — but the
two properties that make the numbers trustworthy:

  * **an absent stage means it did not run**, never a zero. Reporting `rerank: 0` for a
    disabled reranker is the same conflation as mixing up "empty" and "unavailable",
    and it would send the next person hunting for a fast reranker that was never on;
  * **a stage entered twice accumulates**, so a retry or a per-variant loop reports its
    total rather than its last visit.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.retrieval.context_assembly import AssembledContext
from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator, _StageTimings
from callobuzz_rag.retrieval.reranking.base import RerankResult
from callobuzz_rag.vectordb.base import SearchResult

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"


def _settings(**overrides: object) -> MagicMock:
    # Every toggle explicit: an unlisted attribute comes back from MagicMock as a truthy
    # child mock and silently runs a branch the test never asked for.
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
        "mmr_lambda": 0.7,
        "temporal_decay_half_life_days": 30.0,
        "multi_query_count": 3,
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def _build(*, reranked: list[RerankResult] | None = None, **overrides: object) -> QueryOrchestrator:
    embedder = AsyncMock()
    embedding = MagicMock()
    embedding.vector = [0.1] * 8
    embedder.embed_texts = AsyncMock(return_value=[embedding])

    vector_store = AsyncMock()
    vector_store.search = AsyncMock(
        return_value=[
            SearchResult(id=A, score=1.0, payload={"category": "operations"}),
            SearchResult(id=B, score=0.9, payload={"category": "operations"}),
        ]
    )
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
        settings=_settings(**overrides),
        embedder=embedder,
        vector_store=vector_store,
        llm_provider=AsyncMock(),
        context_assembler=assembler,
        reranker=reranker,
    )
    orch._load_chunk_data = AsyncMock(  # type: ignore[method-assign]
        return_value={A: ("text for A", None), B: ("text for B", None)}
    )
    return orch


class TestStageTimingsUnit:
    def test_absent_stage_is_absent_not_zero(self) -> None:
        timings = _StageTimings()
        with timings.stage("embed"):
            pass

        assert "embed" in timings.as_dict()
        # The distinction the whole class exists for.
        assert "rerank" not in timings.as_dict()

    def test_repeated_stage_accumulates(self) -> None:
        timings = _StageTimings()
        for _ in range(3):
            with timings.stage("search_dense"):
                pass

        # Three visits, one entry — a retry reports its total, not its last leg.
        assert list(timings.as_dict()) == ["search_dense"]

    def test_insertion_order_is_pipeline_order(self) -> None:
        timings = _StageTimings()
        for name in ("embed", "search_dense", "fuse", "rerank"):
            with timings.stage(name):
                pass

        assert list(timings.as_dict()) == ["embed", "search_dense", "fuse", "rerank"]

    def test_stage_is_recorded_even_when_the_body_raises(self) -> None:
        timings = _StageTimings()
        with pytest.raises(RuntimeError), timings.stage("rerank"):
            raise RuntimeError("cross-encoder died")

        # A stage that failed is the one you most want the duration of.
        assert "rerank" in timings.as_dict()

    def test_summary_is_readable(self) -> None:
        timings = _StageTimings()
        with timings.stage("embed"):
            pass

        assert timings.summary().startswith("embed=")
        assert timings.summary().endswith("ms")

    def test_empty_timings_are_empty(self) -> None:
        assert _StageTimings().as_dict() == {}
        assert _StageTimings().summary() == ""


class TestPipelineReportsItsStages:
    @pytest.mark.asyncio
    async def test_baseline_pipeline_reports_the_stages_it_ran(self) -> None:
        orch = _build()

        result = await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

        assert list(result.stage_timings_ms) == [
            "embed",
            "search_dense",
            "fuse",
            "assemble",
        ]
        assert all(ms >= 0 for ms in result.stage_timings_ms.values())

    @pytest.mark.asyncio
    async def test_disabled_stages_do_not_appear(self) -> None:
        orch = _build()

        result = await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

        for stage in ("rerank", "mmr", "temporal_decay", "search_sparse"):
            assert stage not in result.stage_timings_ms

    @pytest.mark.asyncio
    async def test_reranking_is_reported_when_it_runs(self) -> None:
        orch = _build(
            reranked=[
                RerankResult(index=1, score=0.9, text="text for B"),
                RerankResult(index=0, score=0.1, text="text for A"),
            ]
        )

        result = await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

        # The stage this is about: ~90% of a live retrieval, previously invisible.
        assert "rerank" in result.stage_timings_ms
        assert "load_chunk_text" in result.stage_timings_ms

    @pytest.mark.asyncio
    async def test_hybrid_adds_the_sparse_leg(self) -> None:
        orch = _build(hybrid_search_enabled=True)

        result = await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

        assert "search_sparse" in result.stage_timings_ms
        # And it is attributed separately from the dense legs, which is the point —
        # hybrid was turned on and nothing could say what it cost.
        assert "search_dense" in result.stage_timings_ms

    @pytest.mark.asyncio
    async def test_category_weights_are_attributed(self) -> None:
        orch = _build()

        result = await orch.run_retrieval_pipeline(
            text="q",
            corpus_id=uuid.uuid4(),
            category_weights={"operations": 1.2},
        )

        assert "category_weights" in result.stage_timings_ms

    @pytest.mark.asyncio
    async def test_temporal_decay_is_attributed(self) -> None:
        orch = _build(temporal_decay_enabled=True)

        result = await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

        assert "temporal_decay" in result.stage_timings_ms
