"""Tests for IngestJob re-ingestion fields."""

from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.pipeline.models import JobStatus


def test_ingest_job_has_mode_column():
    """IngestJob should have a mode column."""
    table = IngestJob.__table__
    assert "mode" in table.columns


def test_ingest_job_has_review_metadata_column():
    """IngestJob should have review_metadata JSONB column."""
    table = IngestJob.__table__
    assert "review_metadata" in table.columns


def test_ingest_job_has_expires_at_column():
    """IngestJob should have expires_at column."""
    table = IngestJob.__table__
    assert "expires_at" in table.columns


def test_ingest_job_has_target_document_id_column():
    """IngestJob should have target_document_id column."""
    table = IngestJob.__table__
    assert "target_document_id" in table.columns


def test_ingest_job_mode_defaults_to_auto():
    """IngestJob mode column should default to 'auto'."""
    col = IngestJob.__table__.columns["mode"]
    assert col.server_default is not None
    assert "auto" in str(col.server_default.arg)


def test_job_status_has_pending_review():
    """JobStatus should have PENDING_REVIEW value."""
    assert JobStatus.PENDING_REVIEW == "pending_review"


def test_job_status_has_expired():
    """JobStatus should have EXPIRED value."""
    assert JobStatus.EXPIRED == "expired"
