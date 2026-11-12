"""The one way text records are queued for import.

`POST /ingest/records` and the knowledge articles/FAQ routes both end here, so the
active-document limit, the job row and the task dispatch live in one place. The caller has
already resolved the knowledge base and checked who may write to it.
"""

import logging
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from callobuzz_rag.accounts.caps import check_document_room
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.pipeline.models import JobStatus
from callobuzz_rag.pipeline.records import is_system_record_id

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.ingest_job import IngestJob

logger = logging.getLogger(__name__)

QUEUE_DOWN = "The task queue is unavailable; nothing was queued. Retry later."


async def fail_undispatched(
    session: "AsyncSession", job: "IngestJob", error: Exception, *, step: str = "dispatch"
) -> None:
    """A job the task queue refused: failed, with the reason, instead of `queued` forever.

    Its idempotency key is released, so the client's retry with the same key starts a new
    upload instead of replaying this failed one.
    """
    logger.error("Dispatching ingest job %s failed: %s", job.id, error, exc_info=True)
    try:
        job.status = JobStatus.FAILED
        job.finished_at = datetime.now(UTC)
        job.idempotency_key = None
        job.errors = {
            "error": "The task queue is unavailable; the job was never started. Retry later.",
            "failed_at_step": step,
            "error_type": type(error).__name__,
        }
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.error("Could not mark undispatched job %s failed", job.id, exc_info=True)


def _as_dict(record: Any) -> dict[str, Any]:
    if is_dataclass(record) and not isinstance(record, type):
        return asdict(record)
    return dict(record.model_dump())


async def queue_records(
    session: "AsyncSession",
    corpus: "Corpus",
    records: "Sequence[Any]",
    *,
    mode: str = "records",
    qdrant_collection: str | None = None,
    allow_system: bool = False,
    room_check: "Callable[..., Awaitable[int | None]] | None" = None,
) -> "IngestJob":
    """Queue `records` for `corpus`: refuse reserved ids, check the plan's document limit,
    commit one `IngestJob` and dispatch the import task. Returns the committed job.

    `records` are request records (pydantic) or `TextRecord`s (dataclass). `allow_system` is
    for a platform knowledge base written by a super admin; a customer's ids may not start
    with the engine's reserved `system:` prefix. `room_check` replaces
    `check_document_room` (a route passes its own reference so it stays patchable).
    """
    from callobuzz_rag.db.models.document import Document
    from callobuzz_rag.db.models.ingest_job import IngestJob as Job

    if not allow_system and any(is_system_record_id(r.id) for r in records):
        raise APIError(
            422,
            "Record ids starting with 'system:' are reserved for the platform.",
            "reserved_record_id",
        )

    # Active-document limit (accounts A6): only record ids that are not yet a document of
    # this corpus add one; re-importing existing records is always allowed. Queued
    # imports count until they finish; with a limit, the check holds the tenant's lock
    # until the job below is committed, so concurrent imports serialise.
    async def _records_adds() -> int:
        ids = sorted({str(r.id) for r in records})
        known = await session.execute(
            select(func.count(Document.external_id.distinct())).where(
                Document.corpus_id == corpus.id, Document.external_id.in_(ids)
            )
        )
        return len(ids) - int(known.scalar() or 0)

    pending = await (room_check or check_document_room)(session, corpus.tenant_id, _records_adds)

    job = Job(
        status="queued",
        total_documents=len(records),
        corpus_id=corpus.id,
        mode=mode,
        pending_new_documents=pending,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    from callobuzz_rag.worker.tasks import ingest_records_task

    try:
        ingest_records_task.delay(
            records=[_as_dict(r) for r in records],
            corpus_id=str(corpus.id),
            job_id=str(job.id),
            qdrant_collection=qdrant_collection or corpus.qdrant_collection,
        )
    except Exception as e:
        await fail_undispatched(session, job, e)
        raise HTTPException(status_code=503, detail=QUEUE_DOWN) from e
    return job
