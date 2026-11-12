"""Tests for pending review confirm flow."""

import uuid

import pytest

from callobuzz_rag.api.schemas.ingest import ConfirmReviewRequest


def test_confirm_review_request_schema():
    """ConfirmReviewRequest should accept action and target_document_id."""
    req = ConfirmReviewRequest(
        action="replace",
        target_document_id=uuid.uuid4(),
    )
    assert req.action == "replace"
    assert req.target_document_id is not None


def test_confirm_review_request_new_action():
    """ConfirmReviewRequest with action='new' doesn't need target_document_id."""
    req = ConfirmReviewRequest(action="new")
    assert req.action == "new"
    assert req.target_document_id is None


def test_confirm_review_request_validates_action():
    """ConfirmReviewRequest should reject invalid actions."""
    with pytest.raises(ValueError):
        ConfirmReviewRequest(action="invalid")


def test_skip_action_and_its_discard_alias():
    assert ConfirmReviewRequest(action="skip").action == "skip"
    assert ConfirmReviewRequest(action="discard").action == "skip"


# --- skip -----------------------------------------------------------------------------


async def _skip(
    *,
    status: str = "pending_review",
    expired: bool = False,
    action: str = "skip",
    role: str | None = None,
):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from callobuzz_rag.api.routes.ingest import confirm_pending_review

    corpus = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
    job = SimpleNamespace(
        id=uuid.uuid4(),
        corpus_id=corpus.id,
        status=status,
        expires_at=datetime.now(UTC) + timedelta(days=-1 if expired else 1),
        pending_new_documents=1,
        storage_key="raw/x",
        finished_at=None,
    )
    session = AsyncMock()
    found = MagicMock()
    found.scalar_one_or_none.return_value = job
    session.execute = AsyncMock(return_value=found)
    request = MagicMock()
    request.state.corpus = None
    request.state.tenant = None
    request.state.role = role
    request.app.state.settings = MagicMock(s3_bucket="bucket")
    storage = AsyncMock()
    with (
        patch("callobuzz_rag.api.routes.ingest.object_storage", return_value=storage),
        patch("callobuzz_rag.worker.tasks.ingest_document_task") as task,
    ):
        body = ConfirmReviewRequest(action=action)
        try:
            response = await confirm_pending_review(request, job.id, body, session)
        except Exception as exc:
            response = exc
    return response, job, storage, task, session


async def test_skip_drops_the_upload():
    response, job, storage, task, session = await _skip()
    assert response.status == "skipped"
    assert job.status == "skipped"
    assert job.finished_at is not None
    assert job.storage_key is None
    assert job.pending_new_documents == 0  # a skipped upload holds no cap reservation
    storage.delete.assert_awaited_once_with("bucket", "raw/x")
    task.delay.assert_not_called()
    session.commit.assert_awaited()


async def test_skip_is_allowed_for_a_member():
    response, job, _, _, _ = await _skip(role="member")
    assert response.status == "skipped"
    assert job.status == "skipped"


async def test_skipping_twice_gives_the_same_answer_and_deletes_nothing_more():
    response, _, storage, _, _ = await _skip(status="skipped")
    assert response.status == "skipped"
    storage.delete.assert_not_awaited()


@pytest.mark.parametrize("status", ["queued", "completed", "expired"])
async def test_skip_of_a_decided_review_is_the_existing_409(status):
    from fastapi import HTTPException

    error, job, storage, _, _ = await _skip(status=status)
    assert isinstance(error, HTTPException)
    assert error.status_code == 409
    assert job.status == status
    storage.delete.assert_not_awaited()


async def test_skip_after_the_review_period_is_the_existing_410():
    from fastapi import HTTPException

    error, job, storage, _, _ = await _skip(expired=True)
    assert isinstance(error, HTTPException)
    assert error.status_code == 410
    assert job.status == "expired"
    storage.delete.assert_not_awaited()


async def test_a_skipped_job_cannot_be_confirmed_afterwards():
    from fastapi import HTTPException

    error, *_ = await _skip(status="skipped", action="new")
    assert isinstance(error, HTTPException)
    assert error.status_code == 409


def test_skipped_is_a_listable_job_status():
    from callobuzz_rag.pipeline.models import JobStatus

    assert JobStatus.SKIPPED == "skipped"
