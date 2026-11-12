"""Unit tests for ingestion pipeline orchestrator with fully mocked providers."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.chunking.chunker import ChunkerConfig, StructureAwareChunker
from callobuzz_rag.embeddings.base import EmbeddingResult
from callobuzz_rag.enrichment.models import EnrichmentResult
from callobuzz_rag.parsing.errors import CorruptedFileError
from callobuzz_rag.parsing.models import (
    ElementType,
    ParsedDocument,
    ParsedElement,
)
from callobuzz_rag.pipeline.fingerprint import compute_fingerprint
from callobuzz_rag.pipeline.models import JobStatus, PipelineResult
from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator

CORPUS_ID = uuid.uuid4()
FILE_BYTES = b"<html><body><h1>Test</h1><p>Hello world.</p></body></html>"
FILE_NAME = "test.html"
MIME_TYPE = "text/html"


@pytest.fixture
def mock_session():
    """Mock AsyncSession with full DB operations."""
    session = AsyncMock()
    # scalar_one_or_none returns None (no duplicate)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=mock_result)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.get = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.add_all = MagicMock()
    return session


@pytest.fixture
def mock_storage():
    storage = AsyncMock()
    storage.upload = AsyncMock()
    return storage


@pytest.fixture
def mock_parser():
    parser = AsyncMock()
    parser.parse = AsyncMock(
        return_value=ParsedDocument(
            elements=[
                ParsedElement(type=ElementType.HEADING, text="Test", level=1),
                ParsedElement(
                    type=ElementType.PARAGRAPH, text="Hello world. This is a test document."
                ),
            ],
            source_filename="test.html",
            parser_name="docling",
            parser_version="2.75.0",
        )
    )
    return parser


@pytest.fixture
def chunker():
    return StructureAwareChunker(ChunkerConfig(min_tokens=1, overlap_tokens=0))


@pytest.fixture
def mock_enricher():
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


@pytest.fixture
def mock_embedder():
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


@pytest.fixture
def mock_vector_store():
    vs = AsyncMock()
    vs.upsert = AsyncMock()
    return vs


@pytest.fixture
def orchestrator(
    mock_session,
    mock_storage,
    mock_parser,
    chunker,
    mock_enricher,
    mock_embedder,
    mock_vector_store,
):
    return IngestionOrchestrator(
        session=mock_session,
        storage=mock_storage,
        parser=mock_parser,
        chunker=chunker,
        enricher=mock_enricher,
        embedder=mock_embedder,
        vector_store=mock_vector_store,
    )


# --- Fingerprint tests ---


def test_compute_fingerprint():
    fp = compute_fingerprint(b"hello")
    assert len(fp) == 64  # SHA256 hex
    assert fp == compute_fingerprint(b"hello")  # Deterministic


def test_fingerprint_different_input():
    assert compute_fingerprint(b"a") != compute_fingerprint(b"b")


# --- Full pipeline tests ---


async def test_full_pipeline_success(orchestrator):
    """Full pipeline runs through all steps and returns COMPLETED."""
    result = await orchestrator.ingest(FILE_BYTES, FILE_NAME, MIME_TYPE, CORPUS_ID)

    assert result.status == JobStatus.COMPLETED
    assert result.document_id is not None
    assert result.chunks_created > 0
    assert result.vectors_stored > 0
    assert result.error is None


async def test_pipeline_calls_all_providers(
    orchestrator, mock_storage, mock_parser, mock_enricher, mock_embedder, mock_vector_store
):
    """Pipeline invokes all providers in order."""
    await orchestrator.ingest(FILE_BYTES, FILE_NAME, MIME_TYPE, CORPUS_ID)

    mock_storage.upload.assert_awaited_once()
    mock_parser.parse.assert_awaited_once()
    mock_enricher.enrich_batch.assert_awaited_once()
    # embed_texts is called twice in auto mode: once for similarity detection, once for embedding
    assert mock_embedder.embed_texts.await_count == 2
    mock_vector_store.upsert.assert_awaited_once()


async def test_pipeline_creates_db_records(orchestrator, mock_session):
    """Pipeline creates IngestJob, Document, DocumentVersion via session."""
    await orchestrator.ingest(FILE_BYTES, FILE_NAME, MIME_TYPE, CORPUS_ID)

    # add/add_all called for job, doc, version, chunks, metadata, embeddings
    assert mock_session.add.call_count >= 2  # job + document + version
    assert mock_session.add_all.call_count >= 3  # chunks, metadata, embeddings
    mock_session.commit.assert_awaited_once()


async def test_pipeline_status_transitions(orchestrator, mock_session):
    """Job status is updated through pipeline stages via flush calls."""
    await orchestrator.ingest(FILE_BYTES, FILE_NAME, MIME_TYPE, CORPUS_ID)

    # flush is called for each status update + chunk/meta/emb storage
    assert mock_session.flush.await_count >= 5


# --- Dedup tests ---


async def test_duplicate_detection(orchestrator, mock_session):
    """Duplicate file returns early with warning."""
    # Make the dedup query return an existing document
    existing_doc = MagicMock()
    existing_doc.id = uuid.uuid4()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_doc
    mock_session.execute = AsyncMock(return_value=mock_result)

    result = await orchestrator.ingest(FILE_BYTES, FILE_NAME, MIME_TYPE, CORPUS_ID)

    assert result.status == JobStatus.COMPLETED
    assert result.document_id == existing_doc.id
    assert any("Duplicate" in w for w in result.warnings)


async def test_force_bypasses_dedup(orchestrator, mock_session, mock_storage):
    """force=True bypasses dedup check."""
    existing_doc = MagicMock()
    existing_doc.id = uuid.uuid4()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = existing_doc
    mock_session.execute = AsyncMock(return_value=mock_result)

    result = await orchestrator.ingest(FILE_BYTES, FILE_NAME, MIME_TYPE, CORPUS_ID, force=True)

    assert result.status == JobStatus.COMPLETED
    mock_storage.upload.assert_awaited_once()


# --- Error handling tests ---


async def test_parsing_failure(orchestrator, mock_parser):
    """Parsing error returns FAILED with failed_at_step='parsing'."""
    mock_parser.parse = AsyncMock(side_effect=CorruptedFileError("bad file"))

    result = await orchestrator.ingest(FILE_BYTES, FILE_NAME, MIME_TYPE, CORPUS_ID)

    assert result.status == JobStatus.FAILED
    assert result.failed_at_step == "parsing"
    assert "bad file" in (result.error or "")


async def test_embedding_failure(orchestrator, mock_embedder):
    """Embedding API failure returns FAILED."""
    mock_embedder.embed_texts = AsyncMock(side_effect=ConnectionError("API down"))

    result = await orchestrator.ingest(FILE_BYTES, FILE_NAME, MIME_TYPE, CORPUS_ID)

    assert result.status == JobStatus.FAILED
    assert result.error is not None


async def test_enrichment_degradation(orchestrator, mock_enricher):
    """Enrichment failure is non-fatal — pipeline continues with defaults."""
    mock_enricher.enrich_batch = AsyncMock(side_effect=RuntimeError("RAKE crashed"))

    result = await orchestrator.ingest(FILE_BYTES, FILE_NAME, MIME_TYPE, CORPUS_ID)

    assert result.status == JobStatus.COMPLETED
    assert any("Enrichment degraded" in w for w in result.warnings)


# --- Storage key tests ---


async def test_custom_storage_key(orchestrator, mock_storage):
    """Custom storage_key skips upload."""
    result = await orchestrator.ingest(
        FILE_BYTES, FILE_NAME, MIME_TYPE, CORPUS_ID, storage_key="custom/key"
    )

    assert result.status == JobStatus.COMPLETED
    mock_storage.upload.assert_not_awaited()


# --- PipelineResult model tests ---


def test_pipeline_result_defaults():
    result = PipelineResult(job_id=uuid.uuid4(), status=JobStatus.QUEUED)
    assert result.chunks_created == 0
    assert result.vectors_stored == 0
    assert result.warnings == []
    assert result.error is None
    assert result.parse_duration_ms == 0
    assert result.total_duration_ms == 0


async def test_pipeline_timing_metrics(orchestrator):
    """Pipeline populates timing metrics on completion."""
    result = await orchestrator.ingest(FILE_BYTES, FILE_NAME, MIME_TYPE, CORPUS_ID)

    assert result.status == JobStatus.COMPLETED
    assert result.total_duration_ms >= 0
    assert result.parse_duration_ms >= 0
    assert result.chunk_duration_ms >= 0
    assert result.enrich_duration_ms >= 0
    assert result.embed_duration_ms >= 0
    assert result.store_duration_ms >= 0


def test_job_status_values():
    assert JobStatus.QUEUED == "queued"
    assert JobStatus.COMPLETED == "completed"
    assert JobStatus.FAILED == "failed"


# --- Tracing decorator tests ---


def test_trace_decorator_passthrough():
    """No-op trace decorator doesn't alter function behavior."""
    from callobuzz_rag.core.tracing import trace

    @trace("test_op")
    def add(a: int, b: int) -> int:
        return a + b

    assert add(1, 2) == 3


def test_trace_decorator_preserves_function_name():
    """Trace decorator preserves __name__ via functools.wraps."""
    from callobuzz_rag.core.tracing import trace

    @trace("my_span")
    def my_function() -> str:
        return "hello"

    assert my_function.__name__ == "my_function"


# --- Factory function tests ---


def test_chunker_from_settings():
    """chunker_from_settings creates chunker with settings values."""
    from unittest.mock import MagicMock

    from callobuzz_rag.chunking.chunker import chunker_from_settings

    settings = MagicMock()
    settings.chunk_target_tokens = 200
    settings.chunk_min_tokens = 30
    settings.chunk_max_tokens = 800
    settings.chunk_overlap_tokens = 32

    chunker = chunker_from_settings(settings)
    assert chunker.config.target_tokens == 200
    assert chunker.config.min_tokens == 30
    assert chunker.config.max_tokens == 800
    assert chunker.config.overlap_tokens == 32


def test_chunk_result_has_context_preamble():
    """ChunkResult has context_preamble field for Phase 2 contextual chunking."""
    from callobuzz_rag.chunking.models import ChunkResult

    chunk = ChunkResult(text="test", chunk_index=0, structure_path=["doc"])
    assert chunk.context_preamble is None
    chunk.context_preamble = "This section discusses testing."
    assert chunk.context_preamble == "This section discusses testing."


# --- Magic byte validation tests ---


def test_validate_content_type_pdf():
    """PDF magic bytes match application/pdf."""
    from callobuzz_rag.api.routes.ingest import _validate_content_type

    assert _validate_content_type(b"%PDF-1.4 content", "application/pdf") is True
    assert _validate_content_type(b"%PDF-1.4 content", "image/png") is False


def test_validate_content_type_png():
    """PNG magic bytes match image/png."""
    from callobuzz_rag.api.routes.ingest import _validate_content_type

    assert _validate_content_type(b"\x89PNG\r\n\x1a\n data", "image/png") is True
    assert _validate_content_type(b"\x89PNG\r\n\x1a\n data", "application/pdf") is False


def test_validate_content_type_html():
    """HTML has no magic bytes — always accepted."""
    from callobuzz_rag.api.routes.ingest import _validate_content_type

    assert _validate_content_type(b"<html>hello</html>", "text/html") is True


def test_validate_content_type_unknown_bytes():
    """Unknown magic bytes reject non-HTML types."""
    from callobuzz_rag.api.routes.ingest import _validate_content_type

    assert _validate_content_type(b"random garbage data", "application/pdf") is False


async def test_document_metadata_is_kept_in_postgres(orchestrator, mock_session):
    """Filters live in Qdrant payloads; this copy is what lets a rebuild restore them."""
    from callobuzz_rag.db.models.document import Document

    await orchestrator.ingest(
        FILE_BYTES, FILE_NAME, MIME_TYPE, CORPUS_ID, doc_metadata={"category": "hr", "team": "ops"}
    )
    docs = [c.args[0] for c in mock_session.add.call_args_list if isinstance(c.args[0], Document)]
    assert docs and docs[0].doc_metadata == {"category": "hr", "team": "ops"}
