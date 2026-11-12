"""Search shows a passage stored in several documents once.

The same text can be in many documents (a shared policy page, a repeated pricing table).
Storage keeps every copy - each document owns its points, so deleting, restoring and
re-indexing one never touches another. Search collapses equal `text_hash` values to the
best-scoring copy and lists the other documents as `also_in`.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from callobuzz_rag.pipeline.payload import chunk_payload
from callobuzz_rag.pipeline.text_hash import text_hash
from callobuzz_rag.retrieval.dedupe import ALSO_IN_LIMIT, collapse_duplicate_passages
from callobuzz_rag.retrieval.models import SearchFilters
from callobuzz_rag.retrieval.search import HybridSearcher
from callobuzz_rag.vectordb.base import ChunkPayload, SearchResult

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def point(n: int, doc: uuid.UUID, score: float, text_hash: str | None = "h-shared") -> SearchResult:
    payload = {"chunk_id": str(uuid.UUID(int=n)), "document_id": str(doc), "corpus_id": "c"}
    if text_hash is not None:
        payload["text_hash"] = text_hash
    return SearchResult(id=str(uuid.UUID(int=n)), score=score, payload=payload)


def session_with_documents(*docs: tuple[uuid.UUID, str, datetime]) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    result.all.return_value = list(docs)
    session.execute = AsyncMock(return_value=result)
    return session


# ------------------------------------------------------------------ the collapse


async def test_search_collapses_duplicate_hashes_keeps_best() -> None:
    a, b, c = (uuid.uuid4() for _ in range(3))
    results = [
        point(1, a, 0.90),
        point(2, b, 0.95, "h-other"),
        point(3, c, 0.80),  # the same passage as point 1, in another document
        point(4, b, 0.70, "h-shared"),
    ]
    session = session_with_documents((a, "a.html", NOW), (b, "b.html", NOW), (c, "c.html", NOW))
    kept, also_in = await collapse_duplicate_passages(session, results)

    assert [p.id for p in kept] == [results[0].id, results[1].id]  # order of the best copies
    assert also_in[results[0].id] == [
        {"document_id": str(c), "file_name": "c.html"},
        {"document_id": str(b), "file_name": "b.html"},
    ]
    assert results[1].id not in also_in


async def test_collapse_keeps_the_highest_scoring_copy_even_if_listed_later() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    # Fusion order is by score, but be strict: the best score wins whatever the order.
    results = [point(1, a, 0.50), point(2, b, 0.90)]
    kept, also_in = await collapse_duplicate_passages(
        session_with_documents((a, "a", NOW), (b, "b", NOW)), results
    )
    assert [p.id for p in kept] == [results[1].id]
    assert also_in[results[1].id] == [{"document_id": str(a), "file_name": "a"}]


async def test_ties_go_to_the_most_recently_ingested_document() -> None:
    old, new = uuid.uuid4(), uuid.uuid4()
    results = [point(1, old, 0.77), point(2, new, 0.77)]  # identical text: identical scores
    docs = [(old, "old.html", NOW - timedelta(days=3)), (new, "new.html", NOW)]
    for order in (results, list(reversed(results))):
        kept, also_in = await collapse_duplicate_passages(session_with_documents(*docs), order)
        assert [p.id for p in kept] == [results[1].id]  # deterministic: the newer document
        assert also_in[results[1].id] == [{"document_id": str(old), "file_name": "old.html"}]


async def test_also_in_lists_other_documents() -> None:
    primary = uuid.uuid4()
    others = [uuid.uuid4() for _ in range(8)]
    results = [point(1, primary, 0.99)] + [
        point(10 + i, d, 0.90 - i * 0.01) for i, d in enumerate(others)
    ]
    # A second copy inside the primary's own document is dropped but is not "also in".
    results.append(point(99, primary, 0.5))
    docs = [(primary, "main.pdf", NOW)] + [(d, f"d{i}.pdf", NOW) for i, d in enumerate(others)]
    kept, also_in = await collapse_duplicate_passages(session_with_documents(*docs), results)

    assert len(kept) == 1
    listed = also_in[results[0].id]
    assert len(listed) == ALSO_IN_LIMIT == 5  # capped
    assert [e["file_name"] for e in listed] == ["d0.pdf", "d1.pdf", "d2.pdf", "d3.pdf", "d4.pdf"]
    assert all(e["document_id"] != str(primary) for e in listed)


async def test_chunks_without_a_hash_are_never_collapsed() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    results = [point(1, a, 0.9, None), point(2, b, 0.8, None), point(3, b, 0.7, None)]
    session = session_with_documents()
    kept, also_in = await collapse_duplicate_passages(session, results)
    assert kept == results and also_in == {}
    session.execute.assert_not_called()  # nothing to look up


async def test_no_duplicates_means_no_database_query() -> None:
    a = uuid.uuid4()
    results = [point(1, a, 0.9, "h1"), point(2, a, 0.8, "h2")]
    session = session_with_documents()
    kept, also_in = await collapse_duplicate_passages(session, results)
    assert kept == results and also_in == {}
    session.execute.assert_not_called()


async def test_a_document_missing_from_the_database_is_left_out_of_also_in() -> None:
    a, gone = uuid.uuid4(), uuid.uuid4()
    results = [point(1, a, 0.9), point(2, gone, 0.8)]
    kept, also_in = await collapse_duplicate_passages(
        session_with_documents((a, "a", NOW)), results
    )
    assert len(kept) == 1 and also_in == {}


# ------------------------------------------------------------------ in HybridSearcher


def _searcher(points: list[SearchResult], session: AsyncMock, **settings: object):
    store = AsyncMock()
    store.search = AsyncMock(side_effect=lambda coll, vec, limit, filters: points[:limit])
    store.search_sparse = AsyncMock(return_value=[])
    embedder = MagicMock(model_id="m")
    cache = MagicMock()
    cache.get_or_embed = AsyncMock(return_value=SimpleNamespace(vector=[0.1, 0.2]))
    cfg = SimpleNamespace(
        retrieval_top_k_dense=20,
        retrieval_top_k_sparse=20,
        qdrant_collection="chunks",
        fusion_strategy="rrf",
        rrf_k=60,
        collapse_duplicates=True,
    )
    for key, value in settings.items():
        setattr(cfg, key, value)
    return HybridSearcher(store, embedder, cache, session, cfg), store


def _chunk_rows(points: list[SearchResult]) -> MagicMock:
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = [
        SimpleNamespace(id=uuid.UUID(p.id), text=f"text {p.id[-4:]}", parent_chunk_id=None)
        for p in points
    ]
    return rows


async def test_hybrid_search_collapses_and_reports_also_in() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    points = [point(1, a, 0.95), point(2, b, 0.94), point(3, uuid.uuid4(), 0.50, "h-unique")]
    docs = MagicMock()
    docs.all.return_value = [(a, "a.html", NOW), (b, "b.html", NOW)]
    session = AsyncMock()
    searcher, _ = _searcher(points, session)
    session.execute = AsyncMock(side_effect=[docs, _chunk_rows([points[0], points[2]])])

    results = await searcher.search("warranty", SearchFilters(corpus_id="c"))

    assert [r.chunk_id for r in results] == [points[0].id, points[2].id]
    assert results[0].also_in == [{"document_id": str(b), "file_name": "b.html"}]
    assert results[1].also_in == []


async def test_collapse_overfetch_still_returns_top_k() -> None:
    # Ten passages, each stored twice, sit at the top; ten distinct ones follow.
    pairs = []
    for i in range(10):
        pairs += [point(100 + i, uuid.uuid4(), 0.99 - i * 0.01, f"h{i}")]
        pairs += [point(200 + i, uuid.uuid4(), 0.985 - i * 0.01, f"h{i}")]
    tail = [point(300 + i, uuid.uuid4(), 0.5 - i * 0.01, f"t{i}") for i in range(10)]
    points = pairs + tail

    def run(collapse: bool):
        session = AsyncMock()
        searcher, store = _searcher(points, session, collapse_duplicates=collapse)
        docs = MagicMock()
        docs.all.return_value = [
            (uuid.UUID(p.payload["document_id"]), "f.html", NOW) for p in points
        ]
        lookups = [docs, _chunk_rows(points)] if collapse else [_chunk_rows(points)]
        session.execute = AsyncMock(side_effect=lookups)
        return searcher, store

    searcher, store = run(True)
    results = await searcher.search("warranty", SearchFilters(corpus_id="c"), top_k_dense=20)
    assert store.search.await_args.args[2] == 30  # asked for 50% more, because of the collapse
    assert len(results) >= 20  # the top 20 distinct passages are still there
    hashes = [r for r in results]
    assert len({r.chunk_id for r in hashes}) == len(results)

    searcher, store = run(False)
    session_results = await searcher.search(
        "warranty", SearchFilters(corpus_id="c"), top_k_dense=20
    )
    assert store.search.await_args.args[2] == 20
    assert len(session_results) == 20  # without it, the same top 20 hold only ten passages twice


async def test_collapse_can_be_switched_off_per_call() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    points = [point(1, a, 0.95), point(2, b, 0.94)]
    session = AsyncMock()
    searcher, store = _searcher(points, session)
    session.execute = AsyncMock(return_value=_chunk_rows(points))
    results = await searcher.search(
        "warranty", SearchFilters(corpus_id="c"), top_k_dense=10, collapse_duplicates=False
    )
    assert len(results) == 2
    assert store.search.await_args.args[2] == 10


async def test_collapse_respects_document_filter() -> None:
    """Qdrant filters first: with a document_id filter only that document's points come
    back, so a copy in another document is never what hides them."""
    a = uuid.uuid4()
    only_a = [point(1, a, 0.9, "h-shared"), point(2, a, 0.8, "h-b")]
    session = AsyncMock()
    searcher, store = _searcher(only_a, session)
    session.execute = AsyncMock(return_value=_chunk_rows(only_a))
    results = await searcher.search(
        "warranty", SearchFilters(corpus_id="c", document_id=str(a)), top_k_dense=10
    )
    assert store.search.await_args.args[3]["document_id"] == str(a)  # the filter is Qdrant's
    assert [r.chunk_id for r in results] == [p.id for p in only_a]
    assert all(r.also_in == [] for r in results)


# ------------------------------------------------------------------ payloads


def test_restore_and_rebuild_payload_carry_text_hash() -> None:
    chunk = SimpleNamespace(
        id=uuid.uuid4(),
        document_version_id=uuid.uuid4(),
        page=2,
        is_table=False,
        is_code=False,
        language="en",
        text_hash="0123456789abcdef",
    )
    payload = chunk_payload(chunk, uuid.uuid4(), uuid.uuid4(), {"category": "faq"}, ["a"]).to_dict()
    assert payload["text_hash"] == "0123456789abcdef"

    # A chunk from before the column existed is hashed from its text on the way out.
    legacy = SimpleNamespace(**{**vars(chunk), "text_hash": None, "text": "Free pickup in Pune."})
    assert chunk_payload(legacy, "c", "d", None).to_dict()["text_hash"] == text_hash(
        "Free pickup in Pune."
    )
    # ... and one with neither carries none.
    bare = SimpleNamespace(**{**vars(chunk), "text_hash": None})
    assert "text_hash" not in chunk_payload(bare, "c", "d", None).to_dict()
    assert "text_hash" not in ChunkPayload("c", "c", "d", "v").to_dict()


# ------------------------------------------------------------------ in /query (QueryOrchestrator)


async def test_query_pipeline_collapses_duplicates_and_overfetches() -> None:
    from tests.unit.test_retrieval_score_breakdown import A, B, C, _build

    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()

    def hit(chunk: str, doc: uuid.UUID, score: float, text_hash: str) -> SearchResult:
        return SearchResult(
            id=chunk, score=score, payload={"document_id": str(doc), "text_hash": text_hash}
        )

    dense = [hit(A, doc_a, 0.95, "same"), hit(B, doc_b, 0.94, "same"), hit(C, doc_a, 0.60, "other")]
    orch = _build(dense, collapse_duplicates=True, fusion_strategy="rrf")
    rows = MagicMock()
    rows.all.return_value = [(doc_a, "a.html", NOW), (doc_b, "b.html", NOW)]
    orch._session.execute = AsyncMock(return_value=rows)

    result = await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4(), top_k=20)

    assert [r.id for r in result.top_results] == [A, C]  # B is A's copy
    assert result.also_in == {A: [{"document_id": str(doc_b), "file_name": "b.html"}]}
    assert orch._vector_store.search.await_args.args[2] == 30  # top_k 20 + 50%


async def test_query_pipeline_is_unchanged_with_the_collapse_off() -> None:
    from tests.unit.test_retrieval_score_breakdown import A, B, C, _build

    dense = [
        SearchResult(id=cid, score=1.0 - i * 0.1, payload={"text_hash": "same"})
        for i, cid in enumerate((A, B, C))
    ]
    orch = _build(dense, fusion_strategy="rrf")  # the mock settings carry no collapse flag
    result = await orch.run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4(), top_k=20)
    assert [r.id for r in result.top_results] == [A, B, C]
    assert result.also_in == {}
    assert orch._vector_store.search.await_args.args[2] == 20
