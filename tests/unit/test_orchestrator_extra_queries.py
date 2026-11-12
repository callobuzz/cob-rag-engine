"""extra_queries: the translated variant joins the dense legs and the keyword leg."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from callobuzz_rag.retrieval import sparse
from callobuzz_rag.retrieval.context_assembly import AssembledContext
from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator
from callobuzz_rag.vectordb.base import SearchResult

A = "11111111-1111-4111-8111-111111111111"


def _orch(hybrid: bool) -> QueryOrchestrator:
    settings = MagicMock()
    for k, v in {
        "query_top_k": 20,
        "query_final_k": 3,
        "query_classification_enabled": False,
        "hyde_enabled": False,
        "rrf_k": 60,
        "qdrant_collection": "chunks",
        "mmr_enabled": False,
        "temporal_decay_enabled": False,
        "hybrid_search_enabled": hybrid,
        "pipeline_order_enabled": False,
        "retrieval_top_k_sparse": 20,
        "fusion_strategy": "rrf",
        "multi_query_count": 3,
    }.items():
        setattr(settings, k, v)
    embedding = MagicMock()
    embedding.vector = [0.1] * 8
    embedder = AsyncMock()
    embedder.embed_texts = AsyncMock(side_effect=lambda texts, task: [embedding for _ in texts])
    store = AsyncMock()
    store.search = AsyncMock(return_value=[SearchResult(id=A, score=0.8, payload={})])
    store.search_sparse = AsyncMock(return_value=[])
    assembler = AsyncMock()
    assembler.assemble = AsyncMock(return_value=AssembledContext([], [], 0, 0, 0))
    return QueryOrchestrator(
        session=AsyncMock(),
        settings=settings,
        embedder=embedder,
        vector_store=store,
        llm_provider=AsyncMock(),
        context_assembler=assembler,
    )


async def test_extras_are_embedded_once_and_searched() -> None:
    orch = _orch(hybrid=False)
    result = await orch.run_retrieval_pipeline(
        text="Kothrud mein home collection hota hai?",
        corpus_id=uuid.uuid4(),
        enable_classification=False,
        extra_queries=["Home collection in Kothrud?", "home collection in kothrud?"],
    )
    texts = orch._embedder.embed_texts.await_args.args[0]  # type: ignore[attr-defined]
    assert texts == ["Kothrud mein home collection hota hai?", "Home collection in Kothrud?"]
    assert orch._vector_store.search.await_count == 2  # type: ignore[attr-defined]
    assert result.queries == texts


async def test_keyword_leg_sees_the_translation() -> None:
    orch = _orch(hybrid=True)
    with patch(
        "callobuzz_rag.retrieval.sparse.query_to_sparse_vector",
        wraps=sparse.query_to_sparse_vector,
    ) as spy:
        await orch.run_retrieval_pipeline(
            text="kab khulta hai",
            corpus_id=uuid.uuid4(),
            enable_classification=False,
            extra_queries=["opening hours"],
        )
    assert spy.call_args.args[0] == "kab khulta hai opening hours"


async def test_no_extras_is_unchanged() -> None:
    orch = _orch(hybrid=False)
    result = await orch.run_retrieval_pipeline(
        text="timings?", corpus_id=uuid.uuid4(), enable_classification=False
    )
    assert orch._embedder.embed_texts.await_args.args[0] == ["timings?"]  # type: ignore[attr-defined]
    assert result.queries == ["timings?"]
