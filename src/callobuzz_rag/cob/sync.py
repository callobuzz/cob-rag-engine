"""Keeping COB's knowledge base equal to the docs in the engine, idempotently.

* `maybe_enqueue_sync` runs at app start. One conditional `UPDATE cob_status ... RETURNING`
  decides which of several starting workers enqueues the sync, so exactly one task is sent per
  docs version however many workers start. A sync stuck `queued` or `running` for too long
  (a lost message, a crashed worker) can be enqueued again.
* `run_sync` is the worker side. It takes a session-level advisory lock, ingests every record
  through the normal records pipeline in batches (unchanged records are skipped by their
  fingerprint, so a re-run after a crash is cheap), deletes records the docs no longer produce,
  and records the outcome in `cob_status`. If the engine's embedding model changed it rebuilds
  the collection and re-ingests everything: a system knowledge base never needs an approved
  migration.

The collaborators (`ingest`, `remove`, ...) are arguments, so the logic is tested without a
vector store; `worker/cob_tasks.py` wires the real ones.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import and_, or_, text, update

from callobuzz_rag.cob.kb import RECORD_PREFIX, records_for
from callobuzz_rag.cob.platform import backfill_builtin_tag
from callobuzz_rag.cob.system import get_system_corpus
from callobuzz_rag.cob.types import DocsIndex
from callobuzz_rag.db.models.cob_status import CobStatus
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.pipeline.records import TextRecord

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

BATCH_SIZE: Final = 50
STATUS_ID: Final = 1
# A literal, not a bound parameter: the key is a constant and a plain text() statement can run
# on any session. Session-level, so it lives as long as the sync's connection.
LOCK_SQL_ACQUIRE: Final = "SELECT pg_advisory_lock(7352001)"
LOCK_SQL_RELEASE: Final = "SELECT pg_advisory_unlock(7352001)"
QUEUED_STALE_AFTER: Final = timedelta(minutes=10)
RUNNING_STALE_AFTER: Final = timedelta(hours=1)
ERROR_LIMIT: Final = 500

SessionFactory = Callable[[], Any]
# (batch of records, system corpus id, its collection, the IngestJob id) -> the pipeline's
# outcome as a dict ("status": "completed" | "failed", "error": ...).
IngestFn = Callable[[list[TextRecord], uuid.UUID, str, uuid.UUID], Awaitable[dict[str, Any]]]
# (system corpus id, external ids) -> how many documents were deleted.
RemoveFn = Callable[[uuid.UUID, list[str]], Awaitable[int]]
# (system corpus id, collection, vector size) -> None: drop and create the collection.
RecreateFn = Callable[[uuid.UUID, str, int], Awaitable[None]]
# system corpus id -> the external ids of its system:cob/ documents.
ExistingFn = Callable[[uuid.UUID], Awaitable[set[str]]]


@dataclass
class SyncOutcome:
    skipped: bool = False
    failed: bool = False
    documents: int = 0
    ingested_batches: int = 0
    removed: int = 0
    rebuilt: bool = False
    error: str | None = None


async def _status_row(session: "AsyncSession") -> CobStatus:
    row = await session.get(CobStatus, STATUS_ID)
    if row is None:  # the migration seeds it; recreate it if someone deleted it
        row = CobStatus(id=STATUS_ID, sync_state="idle", document_count=0)
        session.add(row)
        await session.flush()
    return row


async def maybe_enqueue_sync(
    session_factory: SessionFactory,
    index: DocsIndex,
    *,
    delay: Callable[[str], Any],
    now: datetime,
    embedding_changed: bool = False,
) -> bool:
    """Ask for a sync of `index.version` if the knowledge base is not at it. True when this
    call won the right to enqueue (and did), False when it is current or someone else has it.

    `embedding_changed` (the system corpus was built with another embedding than the engine
    uses now) asks for a sync even at the current docs version; the sync then rebuilds. If the
    broker cannot be reached the row is marked `failed`, so the next start enqueues at once."""
    version = index.version
    async with session_factory() as session:
        await _status_row(session)
        queued_cutoff = now - QUEUED_STALE_AFTER
        running_cutoff = now - RUNNING_STALE_AFTER
        if embedding_changed:
            wanted = or_(
                CobStatus.sync_state.in_(("idle", "failed")),
                and_(CobStatus.sync_state == "queued", CobStatus.updated_at < queued_cutoff),
            )
        else:
            wanted = and_(
                CobStatus.docs_version.is_distinct_from(version),
                or_(
                    CobStatus.requested_version.is_distinct_from(version),
                    CobStatus.sync_state == "failed",
                    and_(CobStatus.sync_state == "queued", CobStatus.updated_at < queued_cutoff),
                ),
            )
        stmt = (
            update(CobStatus)
            .where(
                CobStatus.id == STATUS_ID,
                wanted,
                or_(CobStatus.sync_state != "running", CobStatus.updated_at < running_cutoff),
            )
            .values(
                requested_version=version,
                manifest_hash=index.manifest_hash,
                sync_state="queued",
                last_error=None,
                updated_at=now,
            )
            .returning(CobStatus.id)
        )
        won = (await session.execute(stmt)).first() is not None
        await session.commit()
    if won:
        try:
            delay(version)
        except Exception as exc:
            async with session_factory() as session:
                row = await _status_row(session)
                row.sync_state = "failed"
                row.last_error = "The background worker could not be reached."
                row.updated_at = now
                await session.commit()
            logger.warning("COB docs sync could not be queued: %s", exc)
            raise
    return won


async def request_resync(session: "AsyncSession", index: DocsIndex, *, now: datetime) -> bool:
    """The operator's Resync button: queue a forced sync unless one is already queued or
    running (then False). The caller commits and enqueues."""
    stmt = (
        update(CobStatus)
        .where(
            CobStatus.id == STATUS_ID,
            or_(
                CobStatus.sync_state.in_(("idle", "failed")),
                and_(
                    CobStatus.sync_state == "queued",
                    CobStatus.updated_at < now - QUEUED_STALE_AFTER,
                ),
                and_(
                    CobStatus.sync_state == "running",
                    CobStatus.updated_at < now - RUNNING_STALE_AFTER,
                ),
            ),
        )
        .values(
            requested_version=index.version,
            manifest_hash=index.manifest_hash,
            sync_state="queued",
            last_error=None,
            updated_at=now,
        )
        .returning(CobStatus.id)
    )
    await _status_row(session)
    return (await session.execute(stmt)).first() is not None


def _builtin_only(ids: set[str]) -> set[str]:
    return {i for i in ids if i.startswith(RECORD_PREFIX)}


def _batches(records: list[TextRecord]) -> list[list[TextRecord]]:
    return [records[i : i + BATCH_SIZE] for i in range(0, len(records), BATCH_SIZE)]


async def run_sync(
    session_factory: SessionFactory,
    *,
    index: DocsIndex,
    embedding: dict[str, Any],
    ingest: IngestFn,
    remove: RemoveFn,
    existing_ids: ExistingFn,
    recreate_collection: RecreateFn,
    now: Callable[[], datetime],
    force: bool = False,
) -> SyncOutcome:
    """Bring the system knowledge base to `index`. Never raises: a failure is recorded in
    `cob_status` (`failed` + the first 500 characters of the error) and returned."""
    # The lock is session-level, so it belongs to one connection. A session of its own that never
    # commits keeps that connection for the whole sync (the work session commits many times and
    # hands its connection back to the pool each time), and releases on the same connection.
    async with session_factory() as lock_session:
        await lock_session.execute(text(LOCK_SQL_ACQUIRE))
        try:
            async with session_factory() as session:
                return await _sync_locked(
                    session,
                    index=index,
                    embedding=embedding,
                    ingest=ingest,
                    remove=remove,
                    existing_ids=existing_ids,
                    recreate_collection=recreate_collection,
                    now=now,
                    force=force,
                )
        finally:
            try:
                await lock_session.execute(text(LOCK_SQL_RELEASE))
            except Exception:  # the connection is gone; the lock went with it
                logger.debug("Could not release the COB sync lock", exc_info=True)


async def _sync_locked(
    session: "AsyncSession",
    *,
    index: DocsIndex,
    embedding: dict[str, Any],
    ingest: IngestFn,
    remove: RemoveFn,
    existing_ids: ExistingFn,
    recreate_collection: RecreateFn,
    now: Callable[[], datetime],
    force: bool,
) -> SyncOutcome:
    status = await _status_row(session)
    current = await get_system_corpus(session)
    if current is not None and await backfill_builtin_tag(session, current.id):
        await session.commit()  # documents synced before platform docs existed get their tag
    embedding_changed = current is None or (current.applied_embedding or {}) != embedding
    if not force and status.docs_version == index.version and not embedding_changed:
        if status.sync_state != "idle":
            status.sync_state = "idle"
            status.updated_at = now()
            await session.commit()
        return SyncOutcome(skipped=True, documents=status.document_count)

    status.sync_state = "running"
    status.started_at = now()
    status.updated_at = status.started_at
    status.last_error = None
    await session.commit()

    outcome = SyncOutcome()
    try:
        corpus = await get_system_corpus(session)
        if corpus is None:
            raise RuntimeError("COB's knowledge base does not exist yet")
        corpus_id, collection = corpus.id, corpus.qdrant_collection
        records = records_for(index)
        produced = {r.id for r in records}

        if (corpus.applied_embedding or {}) != embedding:
            # A new embedding model: the old vectors cannot answer, and the fingerprint
            # would skip every unchanged record. Start the collection and the documents over.
            await recreate_collection(corpus_id, collection, int(embedding["dimensions"]))
            outcome.removed += await remove(
                corpus_id, sorted(_builtin_only(await existing_ids(corpus_id)))
            )
            corpus.applied_embedding = dict(embedding)
            await session.commit()
            outcome.rebuilt = True

        for batch in _batches(records):
            job = IngestJob(
                status="queued",
                mode="records",
                corpus_id=corpus_id,
                total_documents=len(batch),
                pending_new_documents=0,
            )
            session.add(job)
            await session.commit()
            result = await ingest(batch, corpus_id, collection, job.id)
            if result.get("status") != "completed":
                raise RuntimeError(
                    f"Importing a batch of docs failed at {result.get('failed_at_step')}: "
                    f"{result.get('error')}"
                )
            outcome.ingested_batches += 1
            # Progress, so "stale" means no progress for an hour, not a long first sync.
            status.updated_at = now()
            await session.commit()

        # Only the engine's own documents are managed here; platform docs (added by the
        # operator, any other id) are never updated or deleted by a sync.
        stale = sorted(_builtin_only(await existing_ids(corpus_id)) - produced)
        if stale:
            outcome.removed += await remove(corpus_id, stale)

        outcome.documents = len(produced)
        status.docs_version = index.version
        status.manifest_hash = index.manifest_hash
        status.requested_version = index.version
        status.document_count = outcome.documents
        status.sync_state = "idle"
        status.synced_at = now()
        status.updated_at = status.synced_at
        status.last_error = None
        await session.commit()
        logger.info(
            "COB docs synced to %s: %d documents, %d removed",
            index.version,
            outcome.documents,
            outcome.removed,
        )
    except Exception as exc:
        logger.error("COB docs sync failed", exc_info=True)
        await session.rollback()
        outcome.failed = True
        outcome.error = str(exc)[:ERROR_LIMIT]
        status = await _status_row(session)
        status.sync_state = "failed"
        status.last_error = outcome.error
        status.updated_at = now()
        await session.commit()
    return outcome
