"""POST /api/v1/ingest: idempotency keys per corpus, replays, and a task queue that is down.

The key used to be unique across every corpus (a clash with another tenant's upload,
and a 409 that revealed it), a retry got a 409 instead of its job, and a broker that
refused `delay()` after the commit left a 500 and a job `queued` forever.
"""

import hashlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from callobuzz_rag.db.models.ingest_job import IngestJob

pytestmark = pytest.mark.usefixtures("unlimited_plan")


@pytest.fixture(autouse=True)
def _no_upload_count() -> object:
    """The upload counter (PIP3) is one more statement on the session; these tests script the
    session's results one by one and have their own tests for what the route stores."""
    with patch("callobuzz_rag.api.routes.ingest._count_upload", AsyncMock()):
        yield


@pytest.fixture(autouse=True)
def upload_alerts() -> object:
    """The 80 % / 100 % upload notices (PIP3) the route schedules in the background, spied on."""
    with patch("callobuzz_rag.api.routes.ingest.alerts.schedule_upload_alerts") as spy:
        yield spy


CONTENT = b"hello world"


class _UniqueViolationError(Exception):
    sqlstate = "23505"


DUPLICATE_KEY = IntegrityError("INSERT", {}, _UniqueViolationError("duplicate key"))


def _request() -> tuple[MagicMock, MagicMock, AsyncMock, SimpleNamespace]:
    corpus = SimpleNamespace(id=uuid.uuid4(), qdrant_collection="corpus_x", tenant_id=uuid.uuid4())
    request = MagicMock()
    request.state.corpus = corpus
    request.state.is_admin = False
    request.state.tenant = None
    request.app.state.settings = MagicMock(
        s3_bucket="b", review_expiry_days=7, max_upload_bytes=50 * 1024 * 1024
    )
    file = MagicMock(content_type="text/plain", filename="a.txt")
    file.read = AsyncMock(return_value=CONTENT)
    session = AsyncMock()
    session.get = AsyncMock(return_value=corpus)
    session.add = MagicMock()
    session.refresh = AsyncMock(side_effect=lambda job: setattr(job, "id", uuid.uuid4()))
    return request, file, session, corpus


def _lookups(*jobs: IngestJob | None) -> AsyncMock:
    results = []
    for job in jobs:
        result = MagicMock()
        result.scalar_one_or_none.return_value = job
        results.append(result)
    return AsyncMock(side_effect=results)


def _job(corpus_id: uuid.UUID, content: bytes = CONTENT, status: str = "processing") -> IngestJob:
    return IngestJob(
        id=uuid.uuid4(),
        corpus_id=corpus_id,
        status=status,
        idempotency_key="k1",
        review_metadata={"content_sha256": hashlib.sha256(content).hexdigest()},
    )


async def _upload(request, file, session, *, key: str | None = "k1", delay=None):  # type: ignore[no-untyped-def]
    from callobuzz_rag.api.routes.ingest import upload_document

    storage = AsyncMock()
    with (
        patch("callobuzz_rag.api.routes.ingest.object_storage", return_value=storage),
        patch("callobuzz_rag.worker.tasks.ingest_document_task") as task,
    ):
        if delay is not None:
            task.delay.side_effect = delay
        result = await upload_document(
            request=request,
            file=file,
            corpus_id=None,
            idempotency_key=key,
            mode="auto",
            target_document_id=None,
            auto_confirm=False,
            metadata=None,
            session=session,
        )
    return result, task, storage


async def test_retry_with_the_same_key_returns_the_original_job() -> None:
    request, file, session, corpus = _request()
    original = _job(corpus.id)
    session.execute = _lookups(original)
    result, task, storage = await _upload(request, file, session)
    assert (result.job_id, result.status) == (original.id, "processing")
    session.add.assert_not_called()
    storage.upload.assert_not_called()
    task.delay.assert_not_called()


async def test_same_key_with_other_bytes_is_a_409() -> None:
    request, file, session, corpus = _request()
    session.execute = _lookups(_job(corpus.id, content=b"another file"))
    with pytest.raises(HTTPException) as err:
        await _upload(request, file, session)
    assert err.value.status_code == 409
    assert "different file" in err.value.detail


async def test_the_key_is_looked_up_in_this_corpus_only() -> None:
    request, file, session, corpus = _request()
    session.execute = _lookups(None)
    result, task, _ = await _upload(request, file, session)
    assert result.status == "queued"
    stmt = session.execute.await_args.args[0]
    where = str(stmt.whereclause.compile(compile_kwargs={"literal_binds": True}))
    assert str(corpus.id).replace("-", "") in where.replace("-", "")
    assert "idempotency_key = 'k1'" in where
    job = session.add.call_args.args[0]
    assert job.review_metadata["content_sha256"] == hashlib.sha256(CONTENT).hexdigest()
    task.delay.assert_called_once()


async def test_losing_a_concurrent_insert_returns_the_winners_job() -> None:
    request, file, session, corpus = _request()
    winner = _job(corpus.id, status="queued")
    session.execute = _lookups(None, winner)  # not there yet; there after the clash
    session.flush = AsyncMock(side_effect=DUPLICATE_KEY)
    result, task, storage = await _upload(request, file, session)
    assert result.job_id == winner.id
    session.rollback.assert_awaited()
    storage.upload.assert_not_called()
    task.delay.assert_not_called()


async def test_a_unique_violation_is_not_reported_as_a_missing_corpus() -> None:
    """`_is_unique_violation` decides between these; a mutation of it fails here."""
    request, file, session, _ = _request()
    session.execute = _lookups(None, None)
    session.flush = AsyncMock(side_effect=DUPLICATE_KEY)
    with pytest.raises(HTTPException) as err:
        await _upload(request, file, session)
    assert err.value.status_code == 409


async def test_broker_down_fails_the_job_and_releases_the_key() -> None:
    request, file, session, _ = _request()
    session.execute = _lookups(None)
    with pytest.raises(HTTPException) as err:
        await _upload(request, file, session, delay=ConnectionError("redis down"))
    assert err.value.status_code == 503
    job = session.add.call_args.args[0]
    assert job.status == "failed"
    assert job.idempotency_key is None  # the client's retry starts a new upload
    assert job.errors["failed_at_step"] == "dispatch"
    assert job.finished_at is not None
    assert session.commit.await_count == 2  # the upload, then the failure


async def test_broker_down_discards_the_stored_file() -> None:
    request, file, session, _ = _request()
    session.execute = _lookups(None)
    with (
        patch("callobuzz_rag.api.routes.ingest.discard_object", new=AsyncMock()) as discard,
        pytest.raises(HTTPException),
    ):
        await _upload(request, file, session, delay=ConnectionError("down"))
    job = session.add.call_args.args[0]
    assert discard.await_args.args[1:3] == ("b", job.storage_key)


async def test_records_broker_down_fails_the_job() -> None:
    from callobuzz_rag.api.routes.ingest import ingest_records
    from callobuzz_rag.api.schemas.ingest import IngestRecordsRequest

    request, _, session, _ = _request()
    body = IngestRecordsRequest(records=[{"id": "a", "text": "x"}])
    with (
        patch("callobuzz_rag.worker.tasks.ingest_records_task") as task,
        pytest.raises(HTTPException) as err,
    ):
        task.delay.side_effect = ConnectionError("down")
        await ingest_records(request=request, body=body, session=session)
    assert err.value.status_code == 503
    job = session.add.call_args.args[0]
    assert job.status == "failed"


async def test_confirm_broker_down_keeps_the_job_reviewable() -> None:
    from callobuzz_rag.api.routes.ingest import confirm_pending_review
    from callobuzz_rag.api.schemas.ingest import ConfirmReviewRequest

    request, _, session, corpus = _request()
    job = IngestJob(
        id=uuid.uuid4(),
        corpus_id=corpus.id,
        status="pending_review",
        mode="auto",
        storage_key="k",
        review_metadata={"file_name": "a.txt", "mime_type": "text/plain"},
    )
    session.execute = _lookups(job)
    with (
        patch("callobuzz_rag.worker.tasks.ingest_document_task") as task,
        pytest.raises(HTTPException) as err,
    ):
        task.delay.side_effect = ConnectionError("down")
        await confirm_pending_review(
            request=request,
            job_id=job.id,
            body=ConfirmReviewRequest(action="new"),
            session=session,
        )
    assert err.value.status_code == 503
    assert (job.status, job.mode, job.target_document_id) == ("pending_review", "auto", None)


async def test_a_retry_of_an_accepted_upload_is_not_refused_by_the_upload_limit() -> None:
    """FU-8: the day's upload limit is checked after the replay, like the document limit, so
    a retry whose first answer was lost gets its job back, not a 429."""
    from callobuzz_rag.api.errors import APIError

    request, file, session, corpus = _request()
    original = _job(corpus.id)
    session.execute = _lookups(original)
    refuse = AsyncMock(side_effect=APIError(429, "used", "fair_use_limit"))
    with patch("callobuzz_rag.api.routes.ingest.check_upload_room", refuse):
        result, task, _ = await _upload(request, file, session)
    assert result.job_id == original.id
    refuse.assert_not_awaited()
    task.delay.assert_not_called()


async def test_a_new_upload_past_the_upload_limit_is_still_refused() -> None:
    from callobuzz_rag.api.errors import APIError

    request, file, session, _ = _request()
    session.execute = _lookups(None)
    refuse = AsyncMock(side_effect=APIError(429, "used", "fair_use_limit"))
    with (
        patch("callobuzz_rag.api.routes.ingest.check_upload_room", refuse),
        pytest.raises(APIError) as err,
    ):
        await _upload(request, file, session)
    assert err.value.code == "fair_use_limit"
    session.add.assert_not_called()


async def test_the_job_keeps_the_file_size_for_the_storage_limit() -> None:
    """FU-4: the document row is written later by the worker; the job carries the bytes so
    the storage limit counts the upload while it is queued."""
    request, file, session, _ = _request()
    session.execute = _lookups(None)
    await _upload(request, file, session)
    assert session.add.call_args.args[0].review_metadata["size_bytes"] == len(CONTENT)


async def test_an_accepted_upload_checks_the_upload_alerts_after_its_commit(upload_alerts) -> None:
    """FU-6: uploads and storage have no decision to hang an alert on."""
    request, file, session, corpus = _request()
    session.execute = _lookups(None)
    await _upload(request, file, session)
    upload_alerts.assert_called_once_with(request.app, corpus.tenant_id)
    assert session.commit.await_count >= 1  # scheduled after the job was saved, not before


async def test_a_refused_upload_tells_the_owner_the_limit_was_reached(upload_alerts) -> None:
    from callobuzz_rag.api.errors import APIError

    request, file, session, corpus = _request()
    session.execute = _lookups(None)
    refuse = AsyncMock(side_effect=APIError(429, "used", "fair_use_limit"))
    with (
        patch("callobuzz_rag.api.routes.ingest.check_upload_room", refuse),
        pytest.raises(APIError),
    ):
        await _upload(request, file, session)
    upload_alerts.assert_called_once_with(request.app, corpus.tenant_id)
