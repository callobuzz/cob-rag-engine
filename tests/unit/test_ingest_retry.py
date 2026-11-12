"""POST /api/v1/ingest/jobs/{job_id}/retry: a failed upload runs again from its stored file."""

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.permissions import ROUTE_OPS
from callobuzz_rag.db.models.ingest_job import IngestJob

pytestmark = pytest.mark.usefixtures("unlimited_plan")

CORPUS = uuid.uuid4()


def _job(**overrides: Any) -> IngestJob:
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "corpus_id": CORPUS,
        "status": "failed",
        "mode": "auto",
        "target_document_id": None,
        "storage_key": "raw/c/u",
        "review_metadata": {"file_name": "menu.pdf", "mime_type": "application/pdf"},
    }
    fields.update(overrides)
    return IngestJob(**fields)


def _world(
    job: IngestJob | None, *, exists: bool = True, live_retry: bool = False
) -> tuple[Any, Any, Any, Any]:
    corpus = SimpleNamespace(
        id=CORPUS, tenant_id=uuid.uuid4(), qdrant_collection="corpus_x", is_system=False
    )
    request = MagicMock()
    request.state.corpus = None
    request.state.is_admin = True
    request.state.tenant = None
    request.app.state.settings = MagicMock(s3_bucket="b", review_expiry_days=7)
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = job
    # The first query loads the job; the second asks whether a live retry of it exists.
    live = MagicMock()
    live.first.return_value = (uuid.uuid4(),) if live_retry else None
    session.execute = AsyncMock(side_effect=[result, live])
    session.get = AsyncMock(return_value=corpus)
    session.add = MagicMock()
    session.refresh = AsyncMock(side_effect=lambda row: setattr(row, "id", uuid.uuid4()))
    storage = AsyncMock()
    storage.exists = AsyncMock(return_value=exists)
    return request, session, storage, corpus


async def _retry(request: Any, session: Any, storage: Any, job_id: uuid.UUID, delay: Any = None):  # type: ignore[no-untyped-def]
    from callobuzz_rag.api.routes.ingest import retry_job

    with (
        patch("callobuzz_rag.api.routes.ingest.object_storage", return_value=storage),
        patch("callobuzz_rag.worker.tasks.ingest_document_task") as task,
        patch(
            "callobuzz_rag.api.routes.ingest.authorized_corpus_ids",
            AsyncMock(return_value=None),
        ),
    ):
        if delay is not None:
            task.delay.side_effect = delay
        result = await retry_job(request=request, job_id=job_id, session=session)
    return result, task


async def test_a_failed_upload_runs_again_as_a_new_job() -> None:
    job = _job(mode="new")
    request, session, storage, _ = _world(job)
    result, task = await _retry(request, session, storage, job.id)
    assert result.status == "queued" and result.job_id != job.id
    new = session.add.call_args.args[0]
    assert new.status == "queued" and new.mode == "new" and new.storage_key == "raw/c/u"
    assert new.review_metadata["retry_of"] == str(job.id)
    assert job.status == "failed"  # the old job is untouched
    kwargs = task.delay.call_args.kwargs
    assert kwargs["storage_key"] == "raw/c/u" and kwargs["file_name"] == "menu.pdf"
    assert kwargs["mime_type"] == "application/pdf" and kwargs["mode"] == "new"
    assert kwargs["job_id"] == str(result.job_id) and kwargs["corpus_id"] == str(CORPUS)


async def test_a_retry_keeps_the_uploads_metadata_and_auto_confirm() -> None:
    job = _job(
        review_metadata={
            "file_name": "menu.pdf",
            "mime_type": "application/pdf",
            "doc_metadata": {"lang": "hi"},
            "auto_confirm": True,
        }
    )
    request, session, storage, _ = _world(job)
    _, task = await _retry(request, session, storage, job.id)
    kwargs = task.delay.call_args.kwargs
    assert kwargs["doc_metadata"] == {"lang": "hi"} and kwargs["auto_confirm"] is True
    plain = _job()
    request, session, storage, _ = _world(plain)
    _, task = await _retry(request, session, storage, plain.id)
    assert task.delay.call_args.kwargs["doc_metadata"] is None
    assert task.delay.call_args.kwargs["auto_confirm"] is False


async def test_retrying_twice_is_refused_while_the_first_retry_lives() -> None:
    job = _job()
    request, session, storage, _ = _world(job, live_retry=True)
    with pytest.raises(APIError) as err:
        await _retry(request, session, storage, job.id)
    assert err.value.status_code == 409 and err.value.extra["reason"] == "already_retried"
    session.add.assert_not_called()


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"mode": "records", "storage_key": None, "review_metadata": None}, "records_job"),
        ({"status": "done"}, "not_failed"),
        ({"status": "processing"}, "not_failed"),
        ({"review_metadata": {"file_name": "menu.pdf"}}, "source_unknown"),
        ({"review_metadata": None}, "source_unknown"),
        ({"storage_key": None}, "source_unknown"),
    ],
)
async def test_what_cannot_be_retried_says_why(overrides: dict[str, Any], reason: str) -> None:
    job = _job(**overrides)
    request, session, storage, _ = _world(job)
    with pytest.raises(APIError) as err:
        await _retry(request, session, storage, job.id)
    assert err.value.status_code == 409 and err.value.code == "job_not_retryable"
    assert err.value.extra["reason"] == reason
    session.add.assert_not_called()


async def test_a_missing_stored_file_is_file_missing() -> None:
    job = _job()
    request, session, storage, _ = _world(job, exists=False)
    with pytest.raises(APIError) as err:
        await _retry(request, session, storage, job.id)
    assert err.value.extra["reason"] == "file_missing"


async def test_an_unknown_job_is_a_404() -> None:
    request, session, storage, _ = _world(None)
    with pytest.raises(HTTPException) as err:
        await _retry(request, session, storage, uuid.uuid4())
    assert err.value.status_code == 404


async def test_another_tenants_job_is_refused() -> None:
    from callobuzz_rag.api.routes.ingest import retry_job

    job = _job()
    request, session, storage, _ = _world(job)
    with (
        patch("callobuzz_rag.api.routes.ingest.object_storage", return_value=storage),
        patch(
            "callobuzz_rag.api.routes.ingest.authorized_corpus_ids",
            AsyncMock(return_value=[uuid.uuid4()]),
        ),
        pytest.raises(HTTPException) as err,
    ):
        await retry_job(request=request, job_id=job.id, session=session)
    assert err.value.status_code == 403
    session.add.assert_not_called()


async def test_the_document_limit_applies() -> None:
    job = _job()
    request, session, storage, _ = _world(job)
    cap = APIError(409, "Document limit reached (3)", "document_limit_reached")
    with (
        patch("callobuzz_rag.api.routes.ingest.check_document_room", AsyncMock(side_effect=cap)),
        pytest.raises(APIError) as err,
    ):
        await _retry(request, session, storage, job.id)
    assert err.value.code == "document_limit_reached"
    session.add.assert_not_called()


async def test_a_replace_of_a_known_document_adds_none() -> None:
    job = _job(mode="replace", target_document_id=uuid.uuid4())
    request, session, storage, _ = _world(job)
    room = AsyncMock(return_value=0)
    with patch("callobuzz_rag.api.routes.ingest.check_document_room", room):
        await _retry(request, session, storage, job.id)
    assert room.await_args.args[2] == 0


async def test_a_queue_that_is_down_fails_only_the_new_job() -> None:
    job = _job()
    request, session, storage, _ = _world(job)
    with pytest.raises(HTTPException) as err:
        await _retry(request, session, storage, job.id, delay=RuntimeError("broker down"))
    assert err.value.status_code == 503
    new = session.add.call_args.args[0]
    assert new.status == "failed" and job.status == "failed"
    storage.delete.assert_not_called()


def test_the_route_needs_write_permission() -> None:
    assert ROUTE_OPS[("POST", "/api/v1/ingest/jobs/{job_id}/retry")] == "write"


async def test_a_super_admin_can_retry_a_platform_doc_upload_and_nobody_else_can() -> None:
    job = _job(mode="new")
    request, session, storage, corpus = _world(job)
    corpus.is_system = True
    request.state.auth_kind = "session"
    request.state.user = SimpleNamespace(id=uuid.uuid4())
    result, _ = await _retry(request, session, storage, job.id)
    assert result.status == "queued"

    request, session, storage, corpus = _world(job)
    corpus.is_system = True
    request.state.auth_kind = "admin_key"
    with pytest.raises(APIError) as caught:
        await _retry(request, session, storage, job.id)
    assert caught.value.code == "system_resource_protected"
