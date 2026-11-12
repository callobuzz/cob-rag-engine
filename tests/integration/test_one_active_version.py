"""One active version per document, on real PostgreSQL.

Re-ingest and version restore retired the active version without locking anything, so
two of them racing on one document (or one racing a review finalize, which does lock)
could leave two ACTIVE versions, both searchable. Now they lock the document row and
re-read its versions, and a partial unique index (phase10_004) rules a second active
version out. Upload idempotency keys are unique per corpus (phase10_005) and a retry
gets its original job. Requires Docker.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from callobuzz_rag.chunking.chunker import StructureAwareChunker
from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.embeddings.base import EmbeddingResult
from callobuzz_rag.enrichment.extractive import ExtractiveEnrichmentProvider
from callobuzz_rag.parsing.models import ElementType, ParsedDocument, ParsedElement
from callobuzz_rag.pipeline.models import IngestMode, JobStatus
from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator
from tests.integration.conftest import new_tenant_id

pytestmark = pytest.mark.integration


def _embedder(delay: float = 0.0) -> MagicMock:
    async def embed(texts: list[str], **_: object) -> list[EmbeddingResult]:
        await asyncio.sleep(delay)
        return [
            EmbeddingResult(vector=[0.1] * 8, model="t", model_version="1", dimensions=8)
            for _ in texts
        ]

    embedder = MagicMock()
    embedder.embed_texts = AsyncMock(side_effect=embed)
    embedder.close = AsyncMock()
    return embedder


def _orchestrator(session, text_: str) -> IngestionOrchestrator:  # type: ignore[no-untyped-def]
    async def parse(*_: object) -> ParsedDocument:
        await asyncio.sleep(0.3)  # both ingests are inside the pipeline at once
        return ParsedDocument(
            elements=[
                ParsedElement(type=ElementType.HEADING, text="Pricing", level=1),
                ParsedElement(type=ElementType.PARAGRAPH, text=text_),
            ],
            source_filename="pricing.md",
            parser_name="mock",
        )

    parser = MagicMock()
    parser.parse = AsyncMock(side_effect=parse)
    return IngestionOrchestrator(
        session=session,
        storage=AsyncMock(),
        parser=parser,
        chunker=StructureAwareChunker(),
        enricher=ExtractiveEnrichmentProvider(),
        embedder=_embedder(),
        vector_store=AsyncMock(),
    )


async def _document(factory, versions: int = 1) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:  # type: ignore[no-untyped-def]
    """A corpus and a document whose last version is active; every version has a chunk."""
    async with factory() as s:
        corpus = Corpus(name=f"one-active-{uuid.uuid4().hex[:8]}", tenant_id=await new_tenant_id(s))
        s.add(corpus)
        await s.flush()
        doc = Document(corpus_id=corpus.id, file_name="pricing.md", mime_type="text/markdown")
        s.add(doc)
        await s.flush()
        ids = []
        for i in range(versions):
            v = DocumentVersion(
                document_id=doc.id, status="active" if i == versions - 1 else "inactive"
            )
            s.add(v)
            await s.flush()
            s.add(Chunk(document_version_id=v.id, corpus_id=corpus.id, text=f"Version {i}."))
            ids.append(v.id)
        await s.commit()
        return corpus.id, doc.id, ids


async def _active(factory, document_id: uuid.UUID) -> list[uuid.UUID]:  # type: ignore[no-untyped-def]
    async with factory() as s:
        rows = await s.execute(
            select(DocumentVersion.id).where(
                DocumentVersion.document_id == document_id, DocumentVersion.status == "active"
            )
        )
        return list(rows.scalars())


async def test_the_database_refuses_a_second_active_version(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    _, doc_id, _ = await _document(factory)
    async with factory() as s:
        s.add(DocumentVersion(document_id=doc_id, status="active"))
        with pytest.raises(IntegrityError):
            await s.flush()


async def test_concurrent_reingests_leave_one_active_version(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    corpus_id, doc_id, _ = await _document(factory)

    async def reingest(label: str) -> JobStatus:
        async with factory() as s:
            result = await _orchestrator(s, f"Price list {label}. " * 20).ingest(
                file_bytes=f"pricing {label} {uuid.uuid4()}".encode(),
                file_name="pricing.md",
                mime_type="text/markdown",
                corpus_id=corpus_id,
                storage_key="k",
                mode=IngestMode.REPLACE,
                target_document_id=doc_id,
            )
            assert result.error is None, result.error
            return result.status

    statuses = await asyncio.gather(reingest("a"), reingest("b"))

    assert statuses == [JobStatus.COMPLETED, JobStatus.COMPLETED]
    assert len(await _active(factory, doc_id)) == 1
    async with factory() as s:
        count = await s.scalar(select(func.count()).where(DocumentVersion.document_id == doc_id))
    assert count == 3


async def test_concurrent_restores_leave_one_active_version(db_engine) -> None:
    from callobuzz_rag.api.routes.documents import restore_version

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    corpus_id, doc_id, (v0, v1, _v2) = await _document(factory, versions=3)
    from callobuzz_rag.db.models.corpus import Corpus

    async with factory() as s:
        tenant_id = await s.scalar(select(Corpus.tenant_id).where(Corpus.id == corpus_id))
    corpus = SimpleNamespace(
        id=corpus_id,
        tenant_id=tenant_id,  # the plan's document limit applies to its tenant (accounts A6)
        qdrant_collection="c",
        embedding_config=None,
        applied_embedding=None,
    )
    request = MagicMock()
    request.app.state.vector_store = AsyncMock()
    request.app.state.embedder = _embedder(delay=0.3)  # both inside the restore at once
    request.app.state.settings = MagicMock()

    async def restore(version_id: uuid.UUID) -> uuid.UUID:
        async with factory() as s:
            response = await restore_version(request, doc_id, version_id, s, corpus)  # type: ignore[arg-type]
            return response.restored_version_id

    with patch(
        "callobuzz_rag.embeddings.factory.embedder_for_corpus", side_effect=lambda c, st, d: d
    ):
        restored = await asyncio.gather(restore(v0), restore(v1))

    assert restored == [v0, v1]
    active = await _active(factory, doc_id)
    assert len(active) == 1 and active[0] in (v0, v1)


async def test_the_unique_index_exists(db_engine) -> None:
    async with db_engine.connect() as conn:
        names = set(
            (
                await conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = 'document_versions'")
                )
            ).scalars()
        )
    assert "uq_document_versions_one_active" in names


async def _restore_setup(db_engine, *, strip_chunks_of: int | None = None):  # type: ignore[no-untyped-def]
    from callobuzz_rag.db.models.corpus import Corpus

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    corpus_id, doc_id, versions = await _document(factory, versions=2)
    async with factory() as s:
        tenant_id = await s.scalar(select(Corpus.tenant_id).where(Corpus.id == corpus_id))
        if strip_chunks_of is not None:
            await s.execute(
                text("DELETE FROM chunks WHERE document_version_id = :v"),
                {"v": versions[strip_chunks_of]},
            )
            await s.commit()
    corpus = SimpleNamespace(
        id=corpus_id,
        tenant_id=tenant_id,
        qdrant_collection="c",
        embedding_config=None,
        applied_embedding=None,
    )
    store = AsyncMock()
    request = MagicMock()
    request.app.state.vector_store = store
    request.app.state.embedder = _embedder()
    request.app.state.settings = MagicMock()
    return factory, corpus, doc_id, versions, store, request


async def test_restore_of_a_version_without_chunks_changes_nothing(db_engine) -> None:
    """A 422 used to leave the database as it was but the current version's vectors deleted."""
    from callobuzz_rag.api.routes.documents import restore_version

    factory, corpus, doc_id, (v0, v1), store, request = await _restore_setup(
        db_engine, strip_chunks_of=0
    )
    with patch(
        "callobuzz_rag.embeddings.factory.embedder_for_corpus", side_effect=lambda c, st, d: d
    ):
        async with factory() as s:
            with pytest.raises(Exception) as err:
                await restore_version(request, doc_id, v0, s, corpus)  # type: ignore[arg-type]
    assert getattr(err.value, "status_code", None) == 422
    assert await _active(factory, doc_id) == [v1]  # still the active one
    store.delete_by_filter.assert_not_called()  # its vectors were never touched
    store.upsert.assert_not_called()


async def test_restore_whose_vector_write_fails_keeps_the_current_version(db_engine) -> None:
    from callobuzz_rag.api.routes.documents import restore_version

    factory, corpus, doc_id, (v0, v1), store, request = await _restore_setup(db_engine)
    store.upsert.side_effect = ConnectionError("qdrant down")
    with patch(
        "callobuzz_rag.embeddings.factory.embedder_for_corpus", side_effect=lambda c, st, d: d
    ):
        async with factory() as s:
            with pytest.raises(ConnectionError):
                await restore_version(request, doc_id, v0, s, corpus)  # type: ignore[arg-type]
    assert await _active(factory, doc_id) == [v1]
    deleted = [c.args[1] for c in store.delete_by_filter.await_args_list]
    assert deleted == [{"document_version_id": str(v0)}]  # only the half-written restore


async def test_restore_swaps_versions_and_deletes_the_old_vectors_last(db_engine) -> None:
    from callobuzz_rag.api.routes.documents import restore_version

    factory, corpus, doc_id, (v0, v1), store, request = await _restore_setup(db_engine)
    with patch(
        "callobuzz_rag.embeddings.factory.embedder_for_corpus", side_effect=lambda c, st, d: d
    ):
        async with factory() as s:
            await restore_version(request, doc_id, v0, s, corpus)  # type: ignore[arg-type]
    assert await _active(factory, doc_id) == [v0]
    store.upsert.assert_awaited_once()
    assert store.delete_by_filter.await_args.args[1] == {"document_version_id": str(v1)}
