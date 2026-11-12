"""End-to-end ingestion test.

Upload a file -> verify chunks in DB -> verify vectors stored.
Requires Docker (PostgreSQL via testcontainers).
"""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from callobuzz_rag.chunking.chunker import StructureAwareChunker
from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.embeddings.base import EmbeddingResult
from callobuzz_rag.enrichment.extractive import ExtractiveEnrichmentProvider
from callobuzz_rag.parsing.models import ElementType, ParsedDocument, ParsedElement
from callobuzz_rag.pipeline.models import JobStatus
from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator
from tests.integration.conftest import new_tenant_id


@pytest.mark.integration
async def test_full_ingestion_pipeline(db_session):
    """Test the complete pipeline: parse -> chunk -> enrich -> embed -> store.

    Uses real DB session + mocked external providers (parser, embedder, storage).
    """
    # Create a corpus
    corpus = Corpus(
        name="Test Corpus", description="For E2E testing", tenant_id=await new_tenant_id(db_session)
    )
    db_session.add(corpus)
    await db_session.commit()
    await db_session.refresh(corpus)

    # Mock parser — returns structured elements
    mock_parser = AsyncMock()
    mock_parser.parse.return_value = ParsedDocument(
        elements=[
            ParsedElement(type=ElementType.HEADING, text="Test Section", level=1),
            ParsedElement(
                type=ElementType.PARAGRAPH,
                text="This is test content for the E2E pipeline. " * 10,
            ),
        ],
        source_filename="test.pdf",
        parser_name="mock",
    )

    # Mock storage — no-op upload
    mock_storage = AsyncMock()

    # Mock embedder — returns fake 1024-dim vectors
    mock_embedder = AsyncMock()
    mock_embedder.embed_texts.side_effect = lambda texts, **kwargs: [
        EmbeddingResult(vector=[0.1] * 1024, model="test", model_version="1.0", dimensions=1024)
        for _ in texts
    ]

    # Mock vector store — no-op upsert
    mock_vector_store = AsyncMock()

    # Real chunker and enricher
    chunker = StructureAwareChunker()
    enricher = ExtractiveEnrichmentProvider()

    # Wire up orchestrator
    orchestrator = IngestionOrchestrator(
        session=db_session,
        storage=mock_storage,
        parser=mock_parser,
        chunker=chunker,
        enricher=enricher,
        embedder=mock_embedder,
        vector_store=mock_vector_store,
    )

    # Run pipeline
    result = await orchestrator.ingest(
        file_bytes=b"fake pdf content",
        file_name="test.pdf",
        mime_type="application/pdf",
        corpus_id=corpus.id,
        storage_key="corpus/test/raw/test-doc",
    )

    # Assertions
    assert result.status == JobStatus.COMPLETED
    assert result.chunks_created > 0
    assert result.vectors_stored > 0
    assert result.error is None

    # Verify chunks in DB
    chunks_result = await db_session.execute(select(Chunk).where(Chunk.corpus_id == corpus.id))
    chunks = chunks_result.scalars().all()
    assert len(chunks) == result.chunks_created
    assert all(c.text for c in chunks)

    # Verify job completed. Scoped to THIS run's job id: the testcontainer database is
    # session-scoped and shared, so "the one completed job in the table" stopped being
    # true the moment a second test created one, and `scalar_one()` raised
    # MultipleResultsFound in a test that had nothing to do with the change.
    jobs_result = await db_session.execute(
        select(IngestJob).where(
            IngestJob.id == result.job_id,
            IngestJob.status == "completed",
        )
    )
    job = jobs_result.scalar_one()
    assert job.processed_documents == 1
