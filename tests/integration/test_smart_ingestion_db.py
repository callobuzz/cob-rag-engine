"""Smart ingestion against real PostgreSQL: paragraph-hash counts, duplicate passages.

The cascades and the counting query are the database's own, so they are checked here and
not in the unit tests' in-memory stand-ins. Vectors live in the in-memory fake the unit
tests use (a real Qdrant adds nothing to what is checked). Requires Docker.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import inspect, select, text

from callobuzz_rag.chunking.chunker import StructureAwareChunker
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.db.models.kb_block_hash import KbBlockHash
from callobuzz_rag.enrichment.extractive import ExtractiveEnrichmentProvider
from callobuzz_rag.parsing.models import ElementType, ParsedDocument, ParsedElement
from callobuzz_rag.pipeline.boilerplate import SqlBlockHashStore, block_hash
from callobuzz_rag.pipeline.models import IngestMode, JobStatus
from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator
from callobuzz_rag.pipeline.payload import chunk_payload
from callobuzz_rag.retrieval.dedupe import collapse_duplicate_passages
from callobuzz_rag.retrieval.sparse import text_to_sparse_vector
from callobuzz_rag.vectordb.base import VectorPoint
from tests.integration.conftest import new_tenant_id
from tests.unit.test_smart_ingestion import (
    CTA,
    DISCLAIMER,
    FakeEmbedder,
    FakeVectors,
    _embed,
    site_page,
)

pytestmark = pytest.mark.integration

PRICE_TABLE = "Zip replacement costs Rs 450 and a seam re-taping costs Rs 650, both with GST."
UNIQUE_A = "Page A explains how a tent zip is fitted by the workshop in Pune, step by step. " * 3
UNIQUE_B = (
    "Page B explains how a boot sole is bonded by the partner in Dehradun, step by step. " * 3
)


def para(text_: str) -> ParsedElement:
    return ParsedElement(type=ElementType.PARAGRAPH, text=text_)


def heading(text_: str) -> ParsedElement:
    return ParsedElement(type=ElementType.HEADING, text=text_, level=1)


def _page(title: str, unique: str) -> list[ParsedElement]:
    """Two sections: the price list every page shares, then text only this page has."""
    return [heading("Repair prices"), para(PRICE_TABLE), heading(title), para(unique)]


async def _corpus(db_session) -> Corpus:
    corpus = Corpus(name=f"smart-{uuid.uuid4().hex[:8]}", tenant_id=await new_tenant_id(db_session))
    db_session.add(corpus)
    await db_session.commit()
    await db_session.refresh(corpus)
    return corpus


def _orchestrator(db_session) -> tuple[IngestionOrchestrator, FakeVectors, AsyncMock]:
    parser = AsyncMock()
    vectors = FakeVectors()
    orch = IngestionOrchestrator(
        session=db_session,
        storage=AsyncMock(),
        parser=parser,
        chunker=StructureAwareChunker(),
        enricher=ExtractiveEnrichmentProvider(),
        embedder=FakeEmbedder(),  # type: ignore[arg-type]
        vector_store=vectors,  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
    )
    return orch, vectors, parser


async def _ingest(orch, parser, corpus, name, elements, mode=IngestMode.NEW, target=None):
    parser.parse = AsyncMock(
        return_value=ParsedDocument(elements=elements, parser_name="mock", parser_version="1")
    )
    result = await orch.ingest(
        file_bytes=name.encode() + uuid.uuid4().bytes,
        file_name=name,
        mime_type="text/markdown",
        corpus_id=corpus.id,
        storage_key=f"k/{name}",
        mode=mode,
        target_document_id=target,
        auto_confirm=True,
        job_id=uuid.uuid4(),
    )
    assert result.status == JobStatus.COMPLETED, result.error
    return result


async def _search(db_session, vectors: FakeVectors, corpus: Corpus, query: str):
    hits = await vectors.search("c", _embed(query), 20, {"corpus_id": str(corpus.id)})
    return await collapse_duplicate_passages(db_session, hits)


# ----------------------------------------------------------------- schema


async def test_the_migration_created_the_tables_and_columns(db_engine) -> None:
    async with db_engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
        assert "kb_block_hashes" in tables
        chunk_cols = await conn.run_sync(
            lambda c: {col["name"]: col for col in inspect(c).get_columns("chunks")}
        )
        assert chunk_cols["text_hash"]["nullable"] is True
        indexes = await conn.run_sync(lambda c: inspect(c).get_indexes("chunks"))
        assert any(i["column_names"] == ["corpus_id", "text_hash"] for i in indexes)
        fks = await conn.run_sync(lambda c: inspect(c).get_foreign_keys("kb_block_hashes"))
        assert {fk["referred_table"]: fk["options"].get("ondelete") for fk in fks} == {
            "corpora": "CASCADE",
            "documents": "CASCADE",
        }


# ----------------------------------------------------------------- counts


async def test_block_counts_follow_document_delete_in_postgres(db_session) -> None:
    corpus = await _corpus(db_session)
    orch, _, parser = _orchestrator(db_session)
    results = [await _ingest(orch, parser, corpus, f"page-{n}.md", site_page(n)) for n in range(4)]
    # The fourth page found the disclaimer in three other documents and dropped it.
    assert any("blocks stripped" in w for w in results[3].warnings)

    store = SqlBlockHashStore(db_session)
    wanted = {block_hash(DISCLAIMER), block_hash(CTA)}
    assert await store.counts(corpus.id, results[3].document_id, wanted) == {h: 3 for h in wanted}
    assert await store.counts(corpus.id, uuid.uuid4(), wanted) == {h: 4 for h in wanted}

    # Deleting a document takes its paragraph rows with it (ON DELETE CASCADE).
    first = await db_session.get(Document, results[0].document_id)
    await db_session.delete(first)
    await db_session.commit()
    assert await store.counts(corpus.id, uuid.uuid4(), wanted) == {h: 3 for h in wanted}
    gone = (
        await db_session.execute(
            select(KbBlockHash).where(KbBlockHash.document_id == results[0].document_id)
        )
    ).all()
    assert gone == []

    # Deleting the knowledge base takes the rest.
    await db_session.execute(text("DELETE FROM corpora WHERE id = :id"), {"id": corpus.id})
    await db_session.commit()
    left = (
        await db_session.execute(select(KbBlockHash).where(KbBlockHash.corpus_id == corpus.id))
    ).all()
    assert left == []


async def test_reingest_excludes_own_hashes_in_postgres(db_session) -> None:
    corpus = await _corpus(db_session)
    orch, _, parser = _orchestrator(db_session)
    first = await _ingest(orch, parser, corpus, "page-0.md", site_page(0))
    document_id = first.document_id
    for _ in range(4):  # the same document, replaced again and again, never counts itself
        again = await _ingest(
            orch, parser, corpus, "page-0.md", site_page(0), IngestMode.REPLACE, document_id
        )
        assert not any("stripped" in w for w in again.warnings)
    rows = (
        await db_session.execute(select(KbBlockHash).where(KbBlockHash.document_id == document_id))
    ).all()
    assert len(rows) == 7  # the page's seven paragraphs, once each: replaced, not piled up


# ----------------------------------------------------------------- duplicate passages


async def test_delete_one_of_two_duplicate_docs_keeps_passage_searchable(db_session) -> None:
    corpus = await _corpus(db_session)
    orch, vectors, parser = _orchestrator(db_session)
    a = await _ingest(orch, parser, corpus, "a.md", _page("About A", UNIQUE_A))
    b = await _ingest(orch, parser, corpus, "b.md", _page("About B", UNIQUE_B))

    # Both documents store the price list; search shows it once and says where else it is.
    kept, also_in = await _search(db_session, vectors, corpus, PRICE_TABLE)
    assert len(kept) == 3  # the shared price list once, plus each page's own text
    (primary_id,) = also_in
    primary = next(k for k in kept if k.id == primary_id)
    (listed,) = also_in[primary_id]
    assert {primary.payload["document_id"], listed["document_id"]} == {
        str(a.document_id),
        str(b.document_id),
    }

    # Delete document A the way the API does: its points by document, then its rows.
    await vectors.delete_by_filter("c", {"document_id": str(a.document_id)})
    doc_a = await db_session.get(Document, a.document_id)
    await db_session.delete(doc_a)
    await db_session.commit()

    kept, also_in = await _search(db_session, vectors, corpus, PRICE_TABLE)
    assert {k.payload["document_id"] for k in kept} == {str(b.document_id)}
    assert len(kept) == 2 and also_in == {}  # the price list is still there, still searchable
    stored = [await db_session.get(Chunk, uuid.UUID(k.id)) for k in kept]
    assert any("Rs 450" in c.text for c in stored if c is not None)


async def test_version_restore_with_duplicates(db_session) -> None:
    corpus = await _corpus(db_session)
    orch, vectors, parser = _orchestrator(db_session)
    a1 = await _ingest(orch, parser, corpus, "prices-a.md", _page("About A", UNIQUE_A))
    b = await _ingest(orch, parser, corpus, "prices-b.md", _page("About B", UNIQUE_B))

    # A is replaced by a version without the price list: its old points go, the other
    # document's stay.
    replacement = [
        heading("Withdrawn"),
        para("Prices were withdrawn pending a review by the finance team in Mumbai."),
    ]
    await _ingest(
        orch, parser, corpus, "prices-a.md", replacement, IngestMode.REPLACE, a1.document_id
    )
    kept, also_in = await _search(db_session, vectors, corpus, PRICE_TABLE)
    assert any(k.payload["document_id"] == str(b.document_id) for k in kept)
    assert also_in == {}  # only one document holds the price list now: nothing to collapse

    # Restore the first version: its points are rebuilt from Postgres, as the restore route
    # does it, with the same payload builder.
    old = (
        await db_session.execute(
            select(DocumentVersion).where(
                DocumentVersion.document_id == a1.document_id, DocumentVersion.status == "inactive"
            )
        )
    ).scalar_one()
    chunks = (
        (await db_session.execute(select(Chunk).where(Chunk.document_version_id == old.id)))
        .scalars()
        .all()
    )
    assert chunks and all(c.text_hash for c in chunks)
    doc_a = await db_session.get(Document, a1.document_id)
    for chunk in chunks:
        payload = chunk_payload(chunk, corpus.id, a1.document_id, doc_a.doc_metadata, [])
        sparse = text_to_sparse_vector(chunk.text)
        await vectors.upsert(
            "c",
            [
                VectorPoint(
                    id=str(chunk.id),
                    vector=_embed(chunk.text),
                    payload=payload.to_dict(),
                    sparse_vector=None if sparse.is_empty() else sparse.to_sparse_vector(),
                )
            ],
        )
    kept, also_in = await _search(db_session, vectors, corpus, PRICE_TABLE)
    # The restored copy and the other document's copy are one passage again: shown once,
    # the other listed.
    assert len(also_in) == 1
    (listed,) = also_in.values()
    assert listed[0]["file_name"] in {"prices-a.md", "prices-b.md"}
    price_hash = next(c.text_hash for c in chunks if "Rs 450" in c.text)
    assert len([k for k in kept if k.payload["text_hash"] == price_hash]) == 1


# ----------------------------------------------------------------- reindex passes


async def test_rehash_blocks_seeds_only_documents_without_rows(db_session) -> None:
    from sqlalchemy import delete

    from callobuzz_rag.retrieval.reindex import rehash_blocks

    corpus = await _corpus(db_session)
    orch, _, parser = _orchestrator(db_session)
    old = await _ingest(orch, parser, corpus, "old.md", site_page(0))
    new = await _ingest(orch, parser, corpus, "new.md", site_page(1))
    # `old` predates paragraph hashing: no rows. `new` has the parser's exact ones.
    await db_session.execute(delete(KbBlockHash).where(KbBlockHash.document_id == old.document_id))
    await db_session.commit()
    exact = set(
        (
            await db_session.execute(
                select(KbBlockHash.block_hash).where(KbBlockHash.document_id == new.document_id)
            )
        ).scalars()
    )
    assert exact

    settings = Settings(_env_file=None)
    dry = await rehash_blocks(
        db_session, corpus, apply=False, max_chars=settings.boilerplate_max_chars
    )
    assert dry.documents == 1  # only the document with no rows
    done = await rehash_blocks(
        db_session, corpus, apply=True, max_chars=settings.boilerplate_max_chars
    )
    assert done.documents == 1 and done.hashes > 0

    async def rows(document_id):
        return set(
            (
                await db_session.execute(
                    select(KbBlockHash.block_hash).where(KbBlockHash.document_id == document_id)
                )
            ).scalars()
        )

    assert len(await rows(old.document_id)) == done.hashes
    assert await rows(new.document_id) == exact  # untouched

    again = await rehash_blocks(
        db_session, corpus, apply=True, max_chars=settings.boilerplate_max_chars
    )
    assert again.documents == 0  # nothing left to seed


async def test_backfill_text_hashes_fills_real_rows(db_session) -> None:
    from sqlalchemy import update

    from callobuzz_rag.pipeline.text_hash import text_hash
    from callobuzz_rag.retrieval.reindex import backfill_text_hashes

    corpus = await _corpus(db_session)
    orch, _, parser = _orchestrator(db_session)
    await _ingest(orch, parser, corpus, "a.md", _page("About A", UNIQUE_A))
    await db_session.execute(
        update(Chunk).where(Chunk.corpus_id == corpus.id).values(text_hash=None)
    )
    await db_session.commit()
    store = AsyncMock()

    dry = await backfill_text_hashes(db_session, corpus, store, apply=False)
    assert dry.chunks > 0 and dry.active == dry.chunks
    store.set_payloads.assert_not_called()

    done = await backfill_text_hashes(db_session, corpus, store, apply=True, batch_size=1)
    assert done.chunks == dry.chunks
    chunks = (await db_session.execute(select(Chunk).where(Chunk.corpus_id == corpus.id))).scalars()
    for chunk in chunks:
        assert chunk.text_hash == text_hash(chunk.text)
    assert store.set_payloads.await_count == done.chunks  # one page per chunk
