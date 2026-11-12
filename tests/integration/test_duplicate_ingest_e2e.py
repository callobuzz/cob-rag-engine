"""BUG-8 regression: a duplicate upload must still finish its job.

Re-uploading identical bytes logged ``Duplicate detected`` and returned a COMPLETED
``PipelineResult`` — but returned *before* the orchestrator ever touched the IngestJob
row the API had already created. The row stayed
``{status: "queued", processed_documents: 0, finished_at: null}`` permanently, so a
client polling the job waited forever for work that was already done. A lost state
transition, not backpressure.

The same early-return shape existed on the two "no target document" failure paths, so
those are covered here too.
"""

import uuid
from unittest.mock import AsyncMock

import pytest

from callobuzz_rag.chunking.chunker import StructureAwareChunker
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.embeddings.base import EmbeddingResult
from callobuzz_rag.enrichment.extractive import ExtractiveEnrichmentProvider
from callobuzz_rag.parsing.models import ElementType, ParsedDocument, ParsedElement
from callobuzz_rag.pipeline.models import IngestMode, JobStatus
from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator
from tests.integration.conftest import new_tenant_id

FILE_BYTES = b"BUG-8 duplicate ingest fixture content"


def _orchestrator(db_session):
    """Real chunker/enricher/DB; the external providers are stubbed, as in test_ingest_e2e."""
    parser = AsyncMock()
    parser.parse.return_value = ParsedDocument(
        elements=[
            ParsedElement(type=ElementType.HEADING, text="Duplicate Fixture", level=1),
            ParsedElement(type=ElementType.PARAGRAPH, text="Body text for the fixture. " * 10),
        ],
        source_filename="dup.pdf",
        parser_name="mock",
    )
    embedder = AsyncMock()
    embedder.embed_texts.side_effect = lambda texts, **kwargs: [
        EmbeddingResult(vector=[0.1] * 1024, model="test", model_version="1.0", dimensions=1024)
        for _ in texts
    ]
    return IngestionOrchestrator(
        session=db_session,
        storage=AsyncMock(),
        parser=parser,
        chunker=StructureAwareChunker(),
        enricher=ExtractiveEnrichmentProvider(),
        embedder=embedder,
        vector_store=AsyncMock(),
    )


async def _queued_job(db_session) -> uuid.UUID:
    """Mirror what POST /api/v1/ingest does before dispatching to the worker."""
    job_id = uuid.uuid4()
    db_session.add(IngestJob(id=job_id, status=JobStatus.QUEUED, total_documents=1))
    await db_session.commit()
    return job_id


async def _reload_job(db_session, job_id: uuid.UUID) -> IngestJob:
    job = await db_session.get(IngestJob, job_id)
    assert job is not None
    await db_session.refresh(job)
    return job


@pytest.mark.integration
async def test_duplicate_upload_finishes_its_job(db_session):
    """Second upload of identical bytes: COMPLETED result *and* a settled job row."""
    corpus = Corpus(name="BUG-8 Duplicate Corpus", tenant_id=await new_tenant_id(db_session))
    db_session.add(corpus)
    await db_session.commit()
    await db_session.refresh(corpus)

    orchestrator = _orchestrator(db_session)

    first_job = await _queued_job(db_session)
    first = await orchestrator.ingest(
        file_bytes=FILE_BYTES,
        file_name="dup.pdf",
        mime_type="application/pdf",
        corpus_id=corpus.id,
        storage_key="corpus/dup/raw/1",
        job_id=first_job,
        mode=IngestMode.NEW,
    )
    assert first.status == JobStatus.COMPLETED

    # Same bytes again, under AUTO — the fingerprint dedup path.
    second_job = await _queued_job(db_session)
    second = await orchestrator.ingest(
        file_bytes=FILE_BYTES,
        file_name="dup.pdf",
        mime_type="application/pdf",
        corpus_id=corpus.id,
        storage_key="corpus/dup/raw/2",
        job_id=second_job,
        mode=IngestMode.AUTO,
    )
    assert second.status == JobStatus.COMPLETED
    assert any("Duplicate" in w for w in second.warnings), second.warnings

    # This is BUG-8. The result said COMPLETED; the row said queued, forever.
    job = await _reload_job(db_session, second_job)
    assert job.status == JobStatus.COMPLETED
    assert job.finished_at is not None
    assert job.processed_documents == 1


@pytest.mark.integration
async def test_replace_without_a_target_finishes_its_job(db_session):
    """The other early return: REPLACE with nothing to replace must not strand the job."""
    corpus = Corpus(name="BUG-8 Replace Corpus", tenant_id=await new_tenant_id(db_session))
    db_session.add(corpus)
    await db_session.commit()
    await db_session.refresh(corpus)

    job_id = await _queued_job(db_session)
    result = await _orchestrator(db_session).ingest(
        file_bytes=b"nothing to replace",
        file_name="absent.pdf",
        mime_type="application/pdf",
        corpus_id=corpus.id,
        storage_key="corpus/dup/raw/3",
        job_id=job_id,
        mode=IngestMode.REPLACE,
    )
    assert result.status == JobStatus.FAILED

    job = await _reload_job(db_session, job_id)
    assert job.status == JobStatus.FAILED
    assert job.finished_at is not None
    assert job.errors and "replace" in str(job.errors).lower()


@pytest.mark.integration
async def test_replace_with_a_missing_target_id_finishes_its_job(db_session):
    """And the third: an explicit target_document_id that does not exist."""
    corpus = Corpus(name="BUG-8 Missing Target Corpus", tenant_id=await new_tenant_id(db_session))
    db_session.add(corpus)
    await db_session.commit()
    await db_session.refresh(corpus)

    job_id = await _queued_job(db_session)
    missing = uuid.uuid4()
    result = await _orchestrator(db_session).ingest(
        file_bytes=b"target does not exist",
        file_name="ghost.pdf",
        mime_type="application/pdf",
        corpus_id=corpus.id,
        storage_key="corpus/dup/raw/4",
        job_id=job_id,
        mode=IngestMode.REPLACE,
        target_document_id=missing,
    )
    assert result.status == JobStatus.FAILED

    job = await _reload_job(db_session, job_id)
    assert job.status == JobStatus.FAILED
    assert job.finished_at is not None
