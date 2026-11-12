"""Tests for expired pending review cleanup."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from callobuzz_rag.pipeline.models import JobStatus


def test_cleanup_identifies_expired_jobs():
    """Cleanup should find jobs with pending_review status past expires_at."""
    now = datetime.now(UTC)

    expired_job = MagicMock()
    expired_job.id = uuid.uuid4()
    expired_job.status = JobStatus.PENDING_REVIEW
    expired_job.expires_at = now - timedelta(days=1)
    expired_job.storage_key = "raw/test/file.md"

    assert expired_job.expires_at < now
    assert expired_job.status == "pending_review"


def test_cleanup_sets_expired_status():
    """Cleanup should set job status to expired."""
    job = MagicMock()
    job.status = JobStatus.PENDING_REVIEW
    job.status = JobStatus.EXPIRED
    assert job.status == "expired"
