"""The document limit counts queued ingests and holds under concurrency (accounts A6 fix).

`check_document_room` compares active documents + what in-flight jobs will add
(`ingest_jobs.pending_new_documents`) + this call's additions with the limit, under the
tenant's advisory lock, and returns the number the caller stores on its job. The worker
re-checks with `ensure_document_room_for_job` and fails the job with
`document_limit_reached`. The PostgreSQL behaviour (lock, one-snapshot count, two
concurrent imports) is in tests/integration/test_document_cap_concurrency.py.
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from sqlalchemy.dialects import postgresql

from callobuzz_rag.accounts import caps
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.chunking.chunker import StructureAwareChunker
from callobuzz_rag.core.exceptions import DocumentLimitReachedError
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator
from callobuzz_rag.pipeline.records import TextRecord


def _sql(stmt: Any) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def _limited(limit: int | None) -> Any:
    return patch.object(
        caps, "effective_limits", AsyncMock(return_value=caps.Limits(max_documents=limit))
    )


# ------------------------------------------------------------ the request check


async def test_queued_additions_count_against_the_limit() -> None:
    """3 active + 2 queued at a limit of 5: one more is refused, and the body says why."""
    with (
        _limited(5),
        patch.object(caps, "lock_tenant_documents", AsyncMock()),
        patch.object(caps, "document_usage", AsyncMock(return_value=(3, 2))),
        pytest.raises(APIError) as err,
    ):
        await caps.check_document_room(AsyncMock(), uuid.uuid4(), 1)
    assert err.value.status_code == 409
    assert err.value.code == "document_limit_reached"
    assert err.value.extra == {"limit": 5, "used": 3, "queued": 2, "adding": 1}


async def test_room_left_after_queued_additions_passes_and_returns_the_reservation() -> None:
    with (
        _limited(5),
        patch.object(caps, "lock_tenant_documents", AsyncMock()),
        patch.object(caps, "document_usage", AsyncMock(return_value=(2, 1))),
    ):
        adds = AsyncMock(return_value=2)
        assert await caps.check_document_room(AsyncMock(), uuid.uuid4(), adds) == 2


async def test_the_lock_is_taken_before_the_count() -> None:
    order = MagicMock()
    order.lock = AsyncMock()
    order.usage = AsyncMock(return_value=(0, 0))
    session, tenant = AsyncMock(), uuid.uuid4()
    with (
        _limited(5),
        patch.object(caps, "lock_tenant_documents", order.lock),
        patch.object(caps, "document_usage", order.usage),
    ):
        await caps.check_document_room(session, tenant, 1)
    assert order.mock_calls == [call.lock(session, tenant), call.usage(session, tenant)]


@pytest.mark.parametrize("adds", [0, AsyncMock(return_value=0)])
async def test_adding_nothing_takes_no_lock_and_reserves_zero(adds: Any) -> None:
    """Replace, version, re-import at the cap: 0, and no serialisation."""
    lock = AsyncMock()
    with _limited(5), patch.object(caps, "lock_tenant_documents", lock):
        assert await caps.check_document_room(AsyncMock(), uuid.uuid4(), adds) == 0
    lock.assert_not_awaited()


async def test_an_unlimited_plan_is_one_query_no_lock_no_count() -> None:
    lock, usage, adds = AsyncMock(), AsyncMock(), AsyncMock(return_value=3)
    with (
        _limited(None),
        patch.object(caps, "lock_tenant_documents", lock),
        patch.object(caps, "document_usage", usage),
    ):
        assert await caps.check_document_room(AsyncMock(), uuid.uuid4(), adds) is None
        assert await caps.check_document_room(AsyncMock(), uuid.uuid4(), 1) == 1
    lock.assert_not_awaited()
    usage.assert_not_awaited()
    adds.assert_not_awaited()


async def test_document_usage_is_one_statement_over_in_flight_reservations() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.one.return_value = (4, 3)
    session.execute = AsyncMock(return_value=result)
    job = uuid.uuid4()
    assert await caps.document_usage(session, uuid.uuid4(), exclude_job_id=job) == (4, 3)
    session.execute.assert_awaited_once()
    sql = _sql(session.execute.await_args.args[0])
    assert "count(DISTINCT documents.id)" in sql
    assert "sum(ingest_jobs.pending_new_documents)" in sql
    assert "ingest_jobs.status IN" in sql
    assert "ingest_jobs.pending_new_documents > " in sql
    assert "ingest_jobs.created_at > now() - " in sql
    assert "ingest_jobs.id != " in sql
    assert "corpora.tenant_id = " in sql


def test_pending_review_and_finished_jobs_are_not_in_flight() -> None:
    assert "queued" in caps.IN_FLIGHT_STATUSES and "embedding" in caps.IN_FLIGHT_STATUSES
    for status in ("pending_review", "completed", "failed", "expired"):
        assert status not in caps.IN_FLIGHT_STATUSES


async def test_the_lock_is_a_transaction_scoped_advisory_lock_per_tenant() -> None:
    session = AsyncMock()
    tenant = uuid.uuid4()
    await caps.lock_tenant_documents(session, tenant)
    stmt = session.execute.await_args.args[0]
    assert "pg_advisory_xact_lock" in _sql(stmt) and "hashtext" in _sql(stmt)
    assert str(tenant) in stmt.compile().params.values()


# ------------------------------------------------------------ routes store it


async def test_records_job_stores_its_reservation() -> None:
    from callobuzz_rag.api.routes.ingest import ingest_records
    from callobuzz_rag.api.schemas.ingest import IngestRecordsRequest

    corpus = MagicMock(id=uuid.uuid4(), qdrant_collection="c", tenant_id=uuid.uuid4())
    session = AsyncMock()
    session.add = MagicMock()
    session.get = AsyncMock(return_value=corpus)
    known = MagicMock()
    known.scalar.return_value = 1  # "a" exists; "b" and "c" are new
    session.execute = AsyncMock(return_value=known)
    session.refresh = AsyncMock(side_effect=lambda j: setattr(j, "id", uuid.uuid4()))
    request = MagicMock()
    request.state.corpus = corpus
    request.state.tenant = None
    body = IngestRecordsRequest(records=[{"id": i, "text": "t"} for i in ("a", "b", "c")])
    with (
        _limited(5),
        patch.object(caps, "lock_tenant_documents", AsyncMock()),
        patch.object(caps, "document_usage", AsyncMock(return_value=(1, 0))),
        patch("callobuzz_rag.worker.tasks.ingest_records_task"),
    ):
        await ingest_records(request=request, body=body, session=session)
    job = session.add.call_args.args[0]
    assert isinstance(job, IngestJob)
    assert job.pending_new_documents == 2
    session.commit.assert_awaited()  # the reservation is committed, releasing the lock


# ------------------------------------------------------------ the worker backstop


def _limit_row(tenant: uuid.UUID, limit: int | None) -> MagicMock:
    """Answers both reads of the backstop: the corpus's tenant, then `effective_limits`'s row
    (max_corpora, the plan's six limits, cached flag, overrides, six grant sums)."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = tenant
    result.one_or_none.return_value = (
        None,
        None,
        None,
        limit,
        None,
        None,
        None,
        True,
        {},
        *[0] * 6,
    )
    return result


async def test_worker_check_excludes_its_own_job_and_refuses_over_the_limit() -> None:
    tenant, job = uuid.uuid4(), uuid.uuid4()
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_limit_row(tenant, 2))
    usage = AsyncMock(return_value=(1, 1))
    with (
        patch.object(caps, "document_usage", usage),
        pytest.raises(DocumentLimitReachedError) as err,
    ):
        await caps.ensure_document_room_for_job(session, uuid.uuid4(), job, 1)
    usage.assert_awaited_once_with(session, tenant, exclude_job_id=job)
    assert err.value.details() == {
        "code": "document_limit_reached",
        "limit": 2,
        "used": 1,
        "queued": 1,
        "adding": 1,
    }


async def test_worker_check_passes_with_room_unlimited_or_nothing_to_add() -> None:
    tenant = uuid.uuid4()
    with patch.object(caps, "document_usage", AsyncMock(return_value=(1, 0))):
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_limit_row(tenant, 2))
        await caps.ensure_document_room_for_job(session, uuid.uuid4(), uuid.uuid4(), 1)
    usage = AsyncMock()
    with patch.object(caps, "document_usage", usage):
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_limit_row(tenant, None))
        await caps.ensure_document_room_for_job(session, uuid.uuid4(), uuid.uuid4(), 5)
        idle = AsyncMock()
        await caps.ensure_document_room_for_job(idle, uuid.uuid4(), uuid.uuid4(), 0)
    usage.assert_not_awaited()
    idle.execute.assert_not_awaited()


def _records_orchestrator(
    job: IngestJob, room: AsyncMock
) -> tuple[IngestionOrchestrator, MagicMock, MagicMock]:
    session = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.get = AsyncMock(return_value=job)
    rows = MagicMock()
    rows.scalars.return_value = []
    session.execute = AsyncMock(return_value=rows)
    store = MagicMock()
    store.upsert = AsyncMock()
    embedder = MagicMock()
    embedder.embed_texts = AsyncMock()
    orch = IngestionOrchestrator(
        session=session,
        storage=MagicMock(),
        parser=MagicMock(),
        chunker=StructureAwareChunker(),
        enricher=MagicMock(),
        embedder=embedder,
        vector_store=store,
        collection="corpus_x",
        document_room=room,
    )
    return orch, session, store


async def test_worker_backstop_fails_a_records_job_with_the_code() -> None:
    job = IngestJob(id=uuid.uuid4(), status="queued")
    room = AsyncMock(side_effect=DocumentLimitReachedError(limit=1, used=1, queued=0, adding=2))
    orch, session, store = _records_orchestrator(job, room)
    corpus = uuid.uuid4()
    out = await orch.ingest_records(
        [TextRecord(id="a", text="x"), TextRecord(id="b", text="y")], corpus, job.id
    )
    room.assert_awaited_once_with(session, corpus, job.id, 2)
    assert out.status == "failed" and out.failed_at_step == "document_limit"
    assert job.status == "failed"
    assert job.errors is not None
    assert job.errors["code"] == "document_limit_reached"
    assert (job.errors["limit"], job.errors["used"], job.errors["adding"]) == (1, 1, 2)
    assert "Document limit reached (1)" in job.errors["error"]
    session.rollback.assert_awaited()
    assert not any(
        type(c.args[0]).__name__ == "Document" for c in session.add.call_args_list
    )  # nothing created
    store.upsert.assert_not_called()
    orch._embedder.embed_texts.assert_not_called()


async def test_worker_backstop_fails_a_new_upload_before_any_document() -> None:
    job = IngestJob(id=uuid.uuid4(), status="queued")
    room = AsyncMock(side_effect=DocumentLimitReachedError(limit=1, used=1, queued=0, adding=1))
    orch, session, store = _records_orchestrator(job, room)
    none = MagicMock()
    none.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=none)
    from callobuzz_rag.pipeline.models import IngestMode, JobStatus

    corpus = uuid.uuid4()
    result = await orch.ingest(
        file_bytes=b"hello",
        file_name="a.txt",
        mime_type="text/plain",
        corpus_id=corpus,
        storage_key="raw/x",
        mode=IngestMode.NEW,
        job_id=job.id,
    )
    room.assert_awaited_once_with(session, corpus, job.id, 1)
    assert result.status == JobStatus.FAILED
    assert result.failed_at_step == "document_limit"
    assert job.status == "failed" and job.errors is not None
    assert job.errors["code"] == "document_limit_reached"
    assert not session.add.called  # no Document, no version
    orch._parser.parse.assert_not_called()
    store.upsert.assert_not_called()


async def test_an_update_only_records_job_is_never_checked() -> None:
    """Existing records add nothing: the backstop is not even called."""
    from callobuzz_rag.db.models.document import Document

    corpus = uuid.uuid4()
    record = TextRecord(id="a", text="same")
    existing = Document(
        id=uuid.uuid4(),
        corpus_id=corpus,
        external_id="a",
        content_fingerprint=record.fingerprint(),
    )
    existing.versions = []
    job = IngestJob(id=uuid.uuid4(), status="queued")
    room = AsyncMock()
    orch, session, _store = _records_orchestrator(job, room)
    rows = MagicMock()
    rows.scalars.return_value = [existing]
    session.execute = AsyncMock(return_value=rows)
    out = await orch.ingest_records([record], corpus, job.id)
    assert out.status == "completed" and out.unchanged == 1
    room.assert_not_awaited()
