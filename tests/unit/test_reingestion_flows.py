"""End-to-end flow tests for re-ingestion scenarios.

Tests the full orchestrator pipeline for different ingest modes:
- AUTO mode with no matches (new document)
- AUTO mode with fingerprint match (duplicate skipped)
- AUTO mode with similar documents (pending review)
- NEW mode (always creates fresh document)
- REPLACE mode with target document
- VERSION mode with target document
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

from callobuzz_rag.chunking.chunker import ChunkerConfig, StructureAwareChunker
from callobuzz_rag.embeddings.base import EmbeddingResult
from callobuzz_rag.enrichment.models import EnrichmentResult
from callobuzz_rag.parsing.models import ElementType, ParsedDocument, ParsedElement
from callobuzz_rag.pipeline.models import IngestMode, JobStatus, PipelineResult
from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator

CORPUS_ID = uuid.uuid4()
FILE_BYTES = b"<html><body><h1>Test</h1><p>Hello world content.</p></body></html>"
FILE_NAME = "test-doc.html"
MIME_TYPE = "text/html"


def _mock_session(*, duplicate_doc=None):
    """Create a mock AsyncSession.

    Args:
        duplicate_doc: If set, fingerprint dedup query returns this document.
    """
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = duplicate_doc
    session.execute = AsyncMock(return_value=mock_result)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.get = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.add_all = MagicMock()
    return session


def _mock_parser():
    """Create a mock parser that returns a simple parsed document."""
    parser = AsyncMock()
    parser.parse = AsyncMock(
        return_value=ParsedDocument(
            elements=[
                ParsedElement(type=ElementType.HEADING, text="Test", level=1),
                ParsedElement(
                    type=ElementType.PARAGRAPH,
                    text="Hello world content for testing.",
                ),
            ],
            source_filename=FILE_NAME,
            parser_name="docling",
            parser_version="2.75.0",
        )
    )
    return parser


def _mock_embedder():
    """Create a mock embedder that returns valid embedding results."""
    embedder = AsyncMock()
    embedder.embed_texts = AsyncMock(
        side_effect=lambda texts, **kwargs: [
            EmbeddingResult(
                vector=[0.1] * 1024,
                model="jina-embeddings-v3",
                model_version="jina-embeddings-v3",
                dimensions=1024,
                token_count=10,
            )
            for _ in texts
        ]
    )
    return embedder


def _mock_enricher():
    """Create a mock enricher."""
    enricher = AsyncMock()
    enricher.enrich_batch = AsyncMock(
        side_effect=lambda chunks: [
            EnrichmentResult(
                summary="Test summary",
                keywords=["test"],
                language="en",
                confidence_score=0.85,
            )
            for _ in chunks
        ]
    )
    return enricher


def _make_orchestrator(session=None, **overrides):
    """Create an IngestionOrchestrator with all deps mocked."""
    defaults = {
        "session": session or _mock_session(),
        "storage": AsyncMock(),
        "parser": _mock_parser(),
        "chunker": StructureAwareChunker(ChunkerConfig(min_tokens=1, overlap_tokens=0)),
        "enricher": _mock_enricher(),
        "embedder": _mock_embedder(),
        "vector_store": AsyncMock(),
        "bucket": "test-bucket",
        "collection": "test-collection",
    }
    defaults.update(overrides)
    return IngestionOrchestrator(**defaults), defaults


# --- AUTO mode tests ---


async def test_auto_mode_new_document():
    """AUTO mode with no matches creates a new Document + Version."""
    session = _mock_session()
    orch, _deps = _make_orchestrator(session=session)

    result = await orch.ingest(
        file_bytes=FILE_BYTES,
        file_name=FILE_NAME,
        mime_type=MIME_TYPE,
        corpus_id=CORPUS_ID,
        mode=IngestMode.AUTO,
    )

    assert result.status == JobStatus.COMPLETED
    assert result.document_id is not None
    assert result.replaced_document_id is None
    assert result.chunks_created > 0
    assert result.vectors_stored > 0


async def test_auto_mode_duplicate_fingerprint_skipped():
    """AUTO mode with fingerprint match returns early with duplicate warning."""
    existing_doc = MagicMock()
    existing_doc.id = uuid.uuid4()

    session = _mock_session(duplicate_doc=existing_doc)
    orch, deps = _make_orchestrator(session=session)

    result = await orch.ingest(
        file_bytes=FILE_BYTES,
        file_name=FILE_NAME,
        mime_type=MIME_TYPE,
        corpus_id=CORPUS_ID,
        mode=IngestMode.AUTO,
    )

    assert result.status == JobStatus.COMPLETED
    assert result.document_id == existing_doc.id
    assert any("Duplicate" in w for w in result.warnings)
    # Parser should NOT have been called (early return)
    deps["parser"].parse.assert_not_awaited()


async def test_auto_mode_force_bypasses_fingerprint():
    """AUTO mode with force=True skips fingerprint dedup."""
    existing_doc = MagicMock()
    existing_doc.id = uuid.uuid4()

    session = _mock_session(duplicate_doc=existing_doc)
    orch, deps = _make_orchestrator(session=session)

    result = await orch.ingest(
        file_bytes=FILE_BYTES,
        file_name=FILE_NAME,
        mime_type=MIME_TYPE,
        corpus_id=CORPUS_ID,
        mode=IngestMode.AUTO,
        force=True,
    )

    assert result.status == JobStatus.COMPLETED
    # Force should proceed to parse even with fingerprint match
    deps["parser"].parse.assert_awaited_once()


# --- NEW mode tests ---


async def test_new_mode_always_creates_fresh():
    """NEW mode always creates a new document, skipping all matching."""
    session = _mock_session()
    orch, _deps = _make_orchestrator(session=session)

    result = await orch.ingest(
        file_bytes=FILE_BYTES,
        file_name=FILE_NAME,
        mime_type=MIME_TYPE,
        corpus_id=CORPUS_ID,
        mode=IngestMode.NEW,
    )

    assert result.status == JobStatus.COMPLETED
    assert result.replaced_document_id is None
    assert result.chunks_created > 0


async def test_new_mode_skips_fingerprint_dedup():
    """NEW mode should not check for existing documents at all."""
    # Even if there's a duplicate, NEW mode should ignore it
    existing_doc = MagicMock()
    existing_doc.id = uuid.uuid4()

    # Set up session so fingerprint query would return a match
    session = _mock_session(duplicate_doc=existing_doc)
    orch, _deps = _make_orchestrator(session=session)

    result = await orch.ingest(
        file_bytes=FILE_BYTES,
        file_name=FILE_NAME,
        mime_type=MIME_TYPE,
        corpus_id=CORPUS_ID,
        mode=IngestMode.NEW,
    )

    # Should succeed — NEW mode never checks fingerprint
    assert result.status == JobStatus.COMPLETED
    assert result.replaced_document_id is None
    # No "Duplicate" warnings
    assert not any("Duplicate" in w for w in result.warnings)


# --- REPLACE mode tests ---


async def test_replace_mode_target_not_found():
    """REPLACE mode with missing target document returns FAILED."""
    session = _mock_session()
    orch, _ = _make_orchestrator(session=session)

    target_id = uuid.uuid4()
    result = await orch.ingest(
        file_bytes=FILE_BYTES,
        file_name=FILE_NAME,
        mime_type=MIME_TYPE,
        corpus_id=CORPUS_ID,
        mode=IngestMode.REPLACE,
        target_document_id=target_id,
    )

    assert result.status == JobStatus.FAILED
    assert "not found" in (result.error or "")


# --- Pipeline result model tests ---


def test_pipeline_result_has_replaced_document_id():
    """PipelineResult includes replaced_document_id field."""
    result = PipelineResult(job_id=uuid.uuid4(), status=JobStatus.COMPLETED)
    assert result.replaced_document_id is None

    doc_id = uuid.uuid4()
    result.replaced_document_id = doc_id
    assert result.replaced_document_id == doc_id


def test_pipeline_result_has_similar_documents():
    """PipelineResult includes similar_documents field."""
    result = PipelineResult(job_id=uuid.uuid4(), status=JobStatus.COMPLETED)
    assert result.similar_documents is None or result.similar_documents == []


# --- Ingest mode enum tests ---


def test_ingest_mode_values():
    """IngestMode enum has expected values."""
    assert IngestMode.AUTO == "auto"
    assert IngestMode.NEW == "new"
    assert IngestMode.REPLACE == "replace"
    assert IngestMode.VERSION == "version"


def test_ingest_mode_from_string():
    """IngestMode can be created from string values."""
    assert IngestMode("auto") == IngestMode.AUTO
    assert IngestMode("new") == IngestMode.NEW
    assert IngestMode("replace") == IngestMode.REPLACE
    assert IngestMode("version") == IngestMode.VERSION
