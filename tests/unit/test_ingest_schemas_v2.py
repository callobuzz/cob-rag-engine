"""Tests for updated ingest schemas with re-ingestion support."""

import uuid

from callobuzz_rag.api.schemas.ingest import IngestResponse, SimilarDocumentInfo


def test_ingest_response_has_version_fields():
    """IngestResponse should include version tracking fields."""
    resp = IngestResponse(
        job_id=uuid.uuid4(),
        status="queued",
        message="ok",
        document_id=uuid.uuid4(),
        version_number=2,
        replaced_document_id=uuid.uuid4(),
    )
    assert resp.version_number == 2
    assert resp.replaced_document_id is not None


def test_ingest_response_version_fields_optional():
    """Version fields should be optional (backward compatible)."""
    resp = IngestResponse(
        job_id=uuid.uuid4(),
        status="queued",
    )
    assert resp.document_id is None
    assert resp.version_number is None
    assert resp.replaced_document_id is None
    assert resp.similar_documents is None


def test_similar_document_info_schema():
    """SimilarDocumentInfo should capture candidate details."""
    info = SimilarDocumentInfo(
        document_id=uuid.uuid4(),
        similarity=0.85,
        match_count=15,
    )
    assert info.similarity == 0.85
    assert info.match_count == 15


def test_ingest_response_with_similar_documents():
    """IngestResponse should include similar_documents list."""
    resp = IngestResponse(
        job_id=uuid.uuid4(),
        status="pending_review",
        similar_documents=[
            SimilarDocumentInfo(
                document_id=uuid.uuid4(),
                similarity=0.82,
                match_count=15,
            )
        ],
    )
    assert len(resp.similar_documents) == 1
    assert resp.similar_documents[0].similarity == 0.82
