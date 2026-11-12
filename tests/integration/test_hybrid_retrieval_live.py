"""Hybrid (dense + BM25) retrieval against the REAL stack — no mocks anywhere.

Real Postgres, real Qdrant, real embeddings from whatever RAG_EMBEDDING_PROVIDER
points at. A mocked vector store can tell you a function was called; only this
can tell you whether searching for a SKU actually finds the SKU, which is the
entire reason the BM25 leg exists.

    pytest tests/integration/test_hybrid_retrieval_live.py -m integration

Creates its own corpus, documents, chunks and Qdrant collection, and drops all
of it afterwards. Skips (does not fail) when the stack is not up.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text as sql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from callobuzz_rag.core.config import Settings
from callobuzz_rag.embeddings.factory import create_embedding_provider
from callobuzz_rag.retrieval.context_assembly import ContextAssembler
from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator
from callobuzz_rag.retrieval.safety import ContentSanitizer
from callobuzz_rag.retrieval.sparse import query_to_sparse_vector, text_to_sparse_vector
from callobuzz_rag.vectordb.base import SparseVector, VectorPoint
from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

pytestmark = pytest.mark.integration


# The SKU is the point: "ROLLER-10" is semantically almost identical to every
# other roller product, so dense embeddings cannot separate them. Only the exact
# token can. Every distractor is deliberately about rollers.
SKU = "ROLLER-10"
TARGET_TEXT = f"Inventory: {SKU} Total stock: 150 units. Location: New Jersey Warehouse."
DISTRACTORS = [
    "Inventory: ROLLER-24 Total stock: 42 units. Location: New Jersey Warehouse.",
    "Inventory: ROLLER-36 Total stock: 8 units. Location: Texas Warehouse.",
    "This roller cover features a polyester fabric and includes two end caps.",
    "Paint rollers should be cleaned with warm water immediately after use.",
    "Packing procedure for fragile items: wrap in bubble wrap, mark the box.",
]


class _Fixture:
    def __init__(self) -> None:
        self.corpus_id = uuid.uuid4()
        self.tenant_id: uuid.UUID | None = None
        self.collection = f"hybrid_live_{uuid.uuid4().hex[:10]}"
        self.target_chunk_id: uuid.UUID | None = None


@pytest.fixture
async def live() -> AsyncIterator[tuple[_Fixture, QueryOrchestrator]]:
    settings = Settings()

    try:
        store = QdrantVectorProvider(settings)
        if not await store.health_check():
            pytest.skip(f"Qdrant not reachable at {settings.qdrant_url}")
        embedder = create_embedding_provider(settings)
        engine = create_async_engine(settings.db_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as probe:
            await probe.execute(sql("SELECT 1"))
    except Exception as e:  # pragma: no cover - environment dependent
        pytest.skip(f"Live stack unavailable: {e}")

    fx = _Fixture()
    texts = [TARGET_TEXT, *DISTRACTORS]

    async with factory() as session:
        # Every corpus belongs to a tenant (accounts A1).
        tenant_id = (
            await session.execute(
                # Every tenant has a plan (accounts A6): the default one.
                sql(
                    "INSERT INTO tenants (name, plan_id) "
                    "VALUES (:n, (SELECT id FROM plans WHERE is_default)) RETURNING id"
                ),
                {"n": f"hybrid-live-{fx.corpus_id.hex[:8]}"},
            )
        ).scalar_one()
        fx.tenant_id = tenant_id
        await session.execute(
            sql("INSERT INTO corpora (id, name, tenant_id) VALUES (:i, :n, :t)"),
            {"i": fx.corpus_id, "n": f"hybrid-live-{fx.corpus_id.hex[:8]}", "t": tenant_id},
        )
        doc_id, ver_id = uuid.uuid4(), uuid.uuid4()
        await session.execute(
            sql("INSERT INTO documents (id, corpus_id) VALUES (:i, :c)"),
            {"i": doc_id, "c": fx.corpus_id},
        )
        await session.execute(
            sql(
                "INSERT INTO document_versions (id, document_id) VALUES (:i, :d)"
                " ON CONFLICT DO NOTHING"
            ),
            {"i": ver_id, "d": doc_id},
        )
        chunk_ids = [uuid.uuid4() for _ in texts]
        fx.target_chunk_id = chunk_ids[0]
        for cid, body in zip(chunk_ids, texts, strict=True):
            await session.execute(
                sql(
                    "INSERT INTO chunks (id, document_version_id, corpus_id, text)"
                    " VALUES (:i, :v, :c, :t)"
                ),
                {"i": cid, "v": ver_id, "c": fx.corpus_id, "t": body},
            )
        await session.commit()

        # Index exactly the way the ingest pipeline does: dense embedding plus a
        # sparse vector built from the RAW chunk text. The query side has to
        # agree with this, and that agreement is what the test is checking.
        await store.create_collection(fx.collection, settings.embedding_dimensions)
        embeddings = await embedder.embed_texts(texts, task="retrieval.passage")
        points = []
        for cid, body, emb in zip(chunk_ids, texts, embeddings, strict=True):
            sd = text_to_sparse_vector(body)
            points.append(
                VectorPoint(
                    id=str(cid),
                    vector=emb.vector,
                    payload={"chunk_id": str(cid), "corpus_id": str(fx.corpus_id)},
                    sparse_vector=SparseVector(indices=sd.indices, values=sd.values),
                )
            )
        await store.upsert(fx.collection, points)

        orchestrator = QueryOrchestrator(
            session=session,
            settings=settings,
            embedder=embedder,
            vector_store=store,
            llm_provider=None,  # type: ignore[arg-type]
            context_assembler=ContextAssembler(settings, sanitizer=ContentSanitizer(settings)),
            reranker=None,
            collection_name=fx.collection,
        )

        try:
            yield fx, orchestrator
        finally:
            await store.delete_collection(fx.collection)
            await session.execute(sql("DELETE FROM corpora WHERE id = :i"), {"i": fx.corpus_id})
            # After the corpus: the tenant FK is RESTRICT.
            await session.execute(sql("DELETE FROM tenants WHERE id = :i"), {"i": fx.tenant_id})
            await session.commit()

    await store.close()
    await embedder.close()
    await engine.dispose()


async def _ranked_ids(
    orchestrator: QueryOrchestrator, fx: _Fixture, query: str, *, hybrid: bool
) -> list[str]:
    result = await orchestrator.run_retrieval_pipeline(
        text=query,
        corpus_id=fx.corpus_id,
        enable_hybrid=hybrid,
        # Everything optional off, so a failure points at the sparse leg rather
        # than at reranking, MMR or decay.
        enable_classification=False,
        enable_reranking=False,
        enable_mmr=False,
        enable_temporal_decay=False,
    )
    return [r.id for r in result.top_results]


async def test_hybrid_finds_the_exact_sku(
    live: tuple[_Fixture, QueryOrchestrator],
) -> None:
    """The whole point: an exact identifier that dense retrieval cannot isolate."""
    fx, orchestrator = live
    target = str(fx.target_chunk_id)

    hybrid = await _ranked_ids(orchestrator, fx, SKU, hybrid=True)

    assert hybrid, "hybrid retrieval returned nothing"
    assert hybrid[0] == target, (
        f"searching for {SKU!r} must rank the chunk containing it first; got {hybrid[:3]}"
    )


async def test_the_bm25_leg_independently_retrieves_the_exact_match(
    live: tuple[_Fixture, QueryOrchestrator],
) -> None:
    """The BM25 contribution must be real, not incidental.

    Deliberately NOT asserting "hybrid beats dense". That is true on the 1343-chunk
    production corpus — searching ROLLER-10 there returns the inventory chunk at
    rank 5 under hybrid and not at all under dense — but it is false on a
    six-document fixture, where the embedding has almost nothing to confuse it
    with and ranks the SKU first unaided. A comparative assertion would therefore
    encode corpus size, not behaviour, and would pass or fail for the wrong
    reason. What IS invariant is that the sparse index resolves the exact token,
    so that is what gets asserted.

    This also pins the query/index tokenizer agreement: the sparse vector here is
    built from raw chunk text at index time, and reverting the query side to
    plain `preprocess_query` — which drops the "10" from "ROLLER-10" — moves this
    chunk from rank 1 to rank 2 on this fixture and from rank 1 to rank 8 on the
    production corpus. Both measured, not assumed.
    """
    fx, orchestrator = live
    target = str(fx.target_chunk_id)

    sd = query_to_sparse_vector(SKU)
    results = await orchestrator._vector_store.search_sparse(
        fx.collection,
        SparseVector(indices=sd.indices, values=sd.values),
        10,
        {"corpus_id": str(fx.corpus_id)},
    )

    ids = [r.id for r in results]
    assert ids, "BM25 search returned nothing"
    assert ids[0] == target, (
        f"BM25 must resolve the exact token {SKU!r} to its chunk; got {ids[:3]}"
    )


async def test_natural_language_still_finds_the_right_chunk(
    live: tuple[_Fixture, QueryOrchestrator],
) -> None:
    """Turning on BM25 must not cost recall on ordinary prose.

    The failure mode this guards is stopword-driven noise: search the raw string
    and `what`/`is`/`the`/`for` start scoring, which measurably changed the top
    results on the real corpus before the query text was stopword-filtered.
    """
    fx, orchestrator = live

    hybrid = await _ranked_ids(
        orchestrator, fx, "what is the packing procedure for fragile items", hybrid=True
    )

    assert hybrid, "hybrid retrieval returned nothing"
    async with async_sessionmaker(create_async_engine(Settings().db_url))() as s:
        row = (
            await s.execute(sql("SELECT text FROM chunks WHERE id = :i"), {"i": hybrid[0]})
        ).first()
    assert row is not None
    assert "Packing procedure" in row[0], f"expected the packing chunk first, got {row[0][:80]!r}"


async def test_answer_size_is_controllable_and_does_not_narrow_search(
    live: tuple[_Fixture, QueryOrchestrator],
) -> None:
    """`final_k` is the caller's knob, `top_k` is the engine's.

    Before this, `final_k` was read straight off global Settings, so a caller
    asking for 8 chunks got 5 and had no way to say otherwise — while `top_k`,
    which the schema described as "final chunks returned", actually set the
    SEARCH width. Callers duly set the width to their answer size, which is how
    a production corpus ended up storing `{"top_k": 5}` and feeding its
    cross-encoder exactly as many candidates as it was asked to keep.
    """
    fx, orchestrator = live

    async def ranked(*, top_k: int | None, final_k: int | None) -> list[str]:
        r = await orchestrator.run_retrieval_pipeline(
            text="warehouse stock for rollers",
            corpus_id=fx.corpus_id,
            top_k=top_k,
            final_k=final_k,
            enable_classification=False,
            enable_reranking=False,
            enable_mmr=False,
            enable_temporal_decay=False,
        )
        return [str(c.chunk_id) for c in r.assembled.cited_chunks]

    # The fixture has 6 chunks, so an answer size above the old hardcoded 5 is
    # reachable only if `final_k` genuinely got through.
    assert len(await ranked(top_k=None, final_k=2)) == 2
    assert len(await ranked(top_k=None, final_k=6)) == 6

    # And raising the answer size must not silently widen or narrow search: a
    # width of 2 caps what any final_k can return.
    assert len(await ranked(top_k=2, final_k=6)) == 2


async def test_sparse_leg_is_off_unless_asked(
    live: tuple[_Fixture, QueryOrchestrator],
) -> None:
    """Default-off is a deliberate call: the code ships inert.

    Proven by behaviour rather than by inspecting a flag — with hybrid off, the
    exact-token advantage must not appear.
    """
    fx, orchestrator = live
    settings = Settings()

    assert settings.hybrid_search_enabled is False, "engine default must stay off"

    default_run = await orchestrator.run_retrieval_pipeline(
        text=SKU,
        corpus_id=fx.corpus_id,
        enable_classification=False,
        enable_reranking=False,
        enable_mmr=False,
        enable_temporal_decay=False,
    )
    explicit_dense = await _ranked_ids(orchestrator, fx, SKU, hybrid=False)

    assert [r.id for r in default_run.top_results] == explicit_dense, (
        "omitting enable_hybrid must behave exactly like dense-only"
    )
