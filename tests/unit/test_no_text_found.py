"""A document with no readable text fails its job cleanly; it never reaches Qdrant.

A scanned PDF with OCR off parses to nothing. The chunker then makes zero chunks, the
vector upsert received an empty list and Qdrant answered "Empty update request" - a 500-ish
error the owner could not act on. The job now fails with `no_text_found` and a sentence
that says what to do.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.chunking.chunker import ChunkerConfig, StructureAwareChunker
from callobuzz_rag.core.exceptions import NoTextFoundError
from callobuzz_rag.parsing.models import ElementType, ParsedDocument, ParsedElement
from callobuzz_rag.pipeline.models import IngestMode, JobStatus
from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator
from callobuzz_rag.vectordb.base import VectorPoint
from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

MESSAGE = (
    "We couldn't find readable text in this file. If it's a scanned PDF, enable OCR or "
    "upload a text version."
)


def _pipeline(elements: list[ParsedElement], target_doc=None):
    session = AsyncMock()
    nothing = MagicMock()
    nothing.scalar_one_or_none.return_value = None
    target = MagicMock()
    target.scalar_one_or_none.return_value = target_doc
    # fingerprint check, target lookup, row-locked re-read; later calls find nothing
    session.execute = AsyncMock(side_effect=[nothing, target, target, *[nothing] * 20])
    session.get = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.add_all = MagicMock()
    storage = AsyncMock()
    parser = AsyncMock()
    parser.parse = AsyncMock(
        return_value=ParsedDocument(elements=elements, parser_name="docling", parser_version="x")
    )
    embedder = AsyncMock()
    store = AsyncMock()
    orch = IngestionOrchestrator(
        session=session,
        storage=storage,
        parser=parser,
        chunker=StructureAwareChunker(ChunkerConfig(min_tokens=1, overlap_tokens=0)),
        enricher=AsyncMock(),
        embedder=embedder,
        vector_store=store,
    )
    return orch, session, storage, embedder, store


async def _ingest(orch: IngestionOrchestrator, **kwargs):
    return await orch.ingest(
        file_bytes=b"%PDF-1.7 a scan",
        file_name="scan.pdf",
        mime_type="application/pdf",
        corpus_id=uuid.uuid4(),
        storage_key="corpus/x/raw/y",
        job_id=uuid.uuid4(),
        **kwargs,
    )


@pytest.mark.parametrize(
    "elements",
    [
        [],
        [ParsedElement(type=ElementType.HEADING, text="Only a title", level=1)],
        [ParsedElement(type=ElementType.PARAGRAPH, text="   \n  ")],
    ],
    ids=["nothing", "headings-only", "whitespace-only"],
)
async def test_a_document_without_text_fails_the_job_with_a_coded_error(elements) -> None:
    orch, session, storage, embedder, store = _pipeline(elements)
    failed_job = SimpleNamespace(
        status="parsing", errors=None, finished_at=None, pending_new_documents=1
    )
    session.get = AsyncMock(side_effect=[None, failed_job])

    result = await _ingest(orch)

    assert result.status == JobStatus.FAILED
    assert result.failed_at_step == "no_text"
    assert result.error == MESSAGE
    assert failed_job.status == JobStatus.FAILED
    assert failed_job.errors["code"] == "no_text_found"
    assert failed_job.errors["error"] == MESSAGE
    assert failed_job.errors["failed_at_step"] == "no_text"
    embedder.embed_texts.assert_not_called()  # stopped before any embedding
    store.upsert.assert_not_called()  # and never reached Qdrant
    session.rollback.assert_awaited()  # no document, version or chunk rows
    storage.delete.assert_awaited_once_with("rag-documents", "corpus/x/raw/y")  # staged file gone
    assert failed_job.pending_new_documents == 0  # the document-cap reservation is released


async def test_a_file_the_pipeline_stored_itself_is_removed_too() -> None:
    orch, session, storage, _, _ = _pipeline([])
    session.get = AsyncMock(side_effect=[None, SimpleNamespace(status="parsing", errors=None)])
    result = await orch.ingest(
        file_bytes=b"x",
        file_name="blank.pdf",
        mime_type="application/pdf",
        corpus_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
    )
    assert result.failed_at_step == "no_text"
    storage.upload.assert_awaited_once()
    stored_key = storage.upload.await_args.args[1]
    storage.delete.assert_awaited_once_with("rag-documents", stored_key)


async def test_a_replacement_without_text_leaves_the_old_version_searchable() -> None:
    """Replacing a document with a scan that has no text used to delete the old vectors
    at the start of the job and then fail: search lost a document the database still
    called active. Vectors of a retired version now go only after the new ones are in."""
    old_version = MagicMock(id=uuid.uuid4(), status="active")
    old_doc = MagicMock(id=uuid.uuid4(), file_name="scan.pdf", versions=[old_version])
    orch, session, _, _, store = _pipeline([], target_doc=old_doc)
    session.get = AsyncMock(side_effect=[None, SimpleNamespace(status="parsing", errors=None)])

    result = await _ingest(orch, mode=IngestMode.REPLACE, target_document_id=old_doc.id)

    assert result.failed_at_step == "no_text"
    store.delete_by_filter.assert_not_called()
    store.upsert.assert_not_called()


async def test_a_replacement_with_text_removes_the_old_vectors_after_the_new_are_in() -> None:
    from callobuzz_rag.embeddings.base import EmbeddingResult
    from callobuzz_rag.enrichment.models import EnrichmentResult

    old_version = MagicMock(id=uuid.uuid4(), status="active")
    old_doc = MagicMock(id=uuid.uuid4(), file_name="scan.pdf", versions=[old_version])
    elements = [
        ParsedElement(type=ElementType.HEADING, text="Terms", level=1),
        ParsedElement(type=ElementType.PARAGRAPH, text="Returns are accepted within 7 days."),
    ]
    orch, session, _, embedder, store = _pipeline(elements, target_doc=old_doc)
    session.get = AsyncMock(return_value=None)
    orch._enricher.enrich_batch = AsyncMock(side_effect=lambda cs: [EnrichmentResult() for _ in cs])
    embedder.embed_texts = AsyncMock(
        side_effect=lambda texts, **kw: [
            EmbeddingResult(vector=[0.1], model="m", model_version="1", dimensions=1) for _ in texts
        ]
    )
    calls: list[str] = []
    store.upsert = AsyncMock(side_effect=lambda *a, **k: calls.append("upsert"))
    store.delete_by_filter = AsyncMock(side_effect=lambda *a, **k: calls.append("delete"))

    result = await _ingest(orch, mode=IngestMode.REPLACE, target_document_id=old_doc.id)

    assert result.status == JobStatus.COMPLETED, result.error
    assert old_version.status == "inactive"
    assert calls == ["upsert", "delete"]
    store.delete_by_filter.assert_awaited_once_with(
        "chunks", {"document_version_id": str(old_version.id)}
    )


def test_the_error_carries_its_code_and_message() -> None:
    err = NoTextFoundError()
    assert str(err) == MESSAGE
    assert err.details() == {"code": "no_text_found"}


# --------------------------------------------------------------- the upsert guard


@pytest.fixture
def provider():
    settings = MagicMock(qdrant_url="http://localhost:6333", qdrant_collection="c")
    with patch("callobuzz_rag.vectordb.qdrant.AsyncQdrantClient") as client_cls:
        client = AsyncMock()
        client_cls.return_value = client
        p = QdrantVectorProvider(settings)
        p._client = client
        return p


async def test_upsert_of_an_empty_list_is_a_no_op(provider) -> None:
    await provider.upsert("chunks", [])
    provider._client.upsert.assert_not_called()  # Qdrant answers an empty request with a 400


async def test_upsert_of_points_still_reaches_qdrant(provider) -> None:
    await provider.upsert("chunks", [VectorPoint(id=str(uuid.uuid4()), vector=[0.1])])
    provider._client.upsert.assert_awaited_once()


async def test_a_failed_removal_of_retired_vectors_does_not_undo_the_replacement() -> None:
    """The retired version's vectors go after the commit: if Qdrant refuses the delete the
    new version is already active and committed, and the job still completes (stale
    duplicates are the worst case, never a rolled-back replacement with a new orphan)."""
    from callobuzz_rag.embeddings.base import EmbeddingResult
    from callobuzz_rag.enrichment.models import EnrichmentResult

    old_version = MagicMock(id=uuid.uuid4(), status="active")
    old_doc = MagicMock(id=uuid.uuid4(), file_name="scan.pdf", versions=[old_version])
    elements = [
        ParsedElement(type=ElementType.HEADING, text="Terms", level=1),
        ParsedElement(type=ElementType.PARAGRAPH, text="Returns are accepted within 7 days."),
    ]
    orch, session, _, embedder, store = _pipeline(elements, target_doc=old_doc)
    session.get = AsyncMock(return_value=None)
    orch._enricher.enrich_batch = AsyncMock(side_effect=lambda cs: [EnrichmentResult() for _ in cs])
    embedder.embed_texts = AsyncMock(
        side_effect=lambda texts, **kw: [
            EmbeddingResult(vector=[0.1], model="m", model_version="1", dimensions=1) for _ in texts
        ]
    )
    order: list[str] = []
    session.commit = AsyncMock(side_effect=lambda: order.append("commit"))
    store.delete_by_filter = AsyncMock(side_effect=RuntimeError("qdrant down"))

    result = await _ingest(orch, mode=IngestMode.REPLACE, target_document_id=old_doc.id)

    assert result.status == JobStatus.COMPLETED, result.error
    store.delete_by_filter.assert_awaited()
    session.rollback.assert_not_awaited()
    assert "commit" in order


async def test_the_retired_vectors_are_deleted_only_after_the_commit() -> None:
    from callobuzz_rag.embeddings.base import EmbeddingResult
    from callobuzz_rag.enrichment.models import EnrichmentResult

    old_version = MagicMock(id=uuid.uuid4(), status="active")
    old_doc = MagicMock(id=uuid.uuid4(), file_name="scan.pdf", versions=[old_version])
    elements = [ParsedElement(type=ElementType.PARAGRAPH, text="Returns are accepted.")]
    orch, session, _, embedder, store = _pipeline(elements, target_doc=old_doc)
    session.get = AsyncMock(return_value=None)
    orch._enricher.enrich_batch = AsyncMock(side_effect=lambda cs: [EnrichmentResult() for _ in cs])
    embedder.embed_texts = AsyncMock(
        side_effect=lambda texts, **kw: [
            EmbeddingResult(vector=[0.1], model="m", model_version="1", dimensions=1) for _ in texts
        ]
    )
    order: list[str] = []
    session.commit = AsyncMock(side_effect=lambda: order.append("commit"))
    store.upsert = AsyncMock(side_effect=lambda *a, **k: order.append("upsert"))
    store.delete_by_filter = AsyncMock(side_effect=lambda *a, **k: order.append("delete"))

    await _ingest(orch, mode=IngestMode.REPLACE, target_document_id=old_doc.id)

    assert order.index("upsert") < order.index("commit") < order.index("delete")
