"""Document ingestion endpoints.

Phase 4: Multi-corpus support. Corpus derived from auth middleware,
corpus_id optional when using corpus API key.
"""

import hashlib
import io
import logging
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.caps import check_document_room, check_upload_room, upload_byte_limit
from callobuzz_rag.api.deps import authorized_corpus_ids, get_session, resolve_scope
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.ingest import (
    ConfirmReviewRequest,
    IngestRecordsRequest,
    IngestRecordsResponse,
    IngestResponse,
    JobListResponse,
    JobStatusResponse,
    SimilarDocumentInfo,
)
from callobuzz_rag.api.services.system_documents import refuse_system_target
from callobuzz_rag.cob.platform import allow_system_write, platform_metadata
from callobuzz_rag.cob.system import is_system_corpus
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.fairuse import alerts  # PIP3 uploads
from callobuzz_rag.pipeline.models import JobStatus
from callobuzz_rag.pipeline.records_queue import QUEUE_DOWN, fail_undispatched, queue_records
from callobuzz_rag.storage.cleanup import discard_object
from callobuzz_rag.storage.factory import object_storage
from callobuzz_rag.storage.key_builder import build_raw_document_key
from callobuzz_rag.transcription.base import AUDIO_MIME_TYPES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

# The upload byte cap is `RAG_MAX_UPLOAD_BYTES` (a plan may lower it); the document token cap
# is enforced by the pipeline after parsing, on the real text.

# What a file extension means when the browser sends application/octet-stream.
EXTENSION_MIME_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".html": "text/html",
    ".htm": "text/html",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
}

ALLOWED_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
    "text/html",
    "text/markdown",
    "text/plain",
    "image/png",
    "image/jpeg",
}

MAGIC_SIGNATURES: dict[bytes, set[str]] = {
    b"%PDF": {"application/pdf"},
    b"PK": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    },
    b"\x89PNG": {"image/png"},
    b"\xff\xd8": {"image/jpeg"},
}


def _looks_like_audio(content: bytes) -> bool:
    """Container signatures of the audio formats the transcriber accepts."""
    head = content[:16]
    return (
        head.startswith((b"ID3", b"OggS", b"fLaC", b"\x1aE\xdf\xa3"))  # mp3 tag, ogg, flac, webm
        or (head[:4] == b"RIFF" and head[8:12] == b"WAVE")
        or head[4:8] == b"ftyp"  # m4a / mp4
        or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)  # raw mp3 frame
    )


def _validate_content_type(content: bytes, declared_type: str) -> bool:
    """Validate file content matches declared MIME type via magic bytes."""
    if declared_type in ("text/html", "text/markdown", "text/plain", "text/csv"):
        return True
    if declared_type in AUDIO_MIME_TYPES:
        return _looks_like_audio(content)
    for signature, valid_types in MAGIC_SIGNATURES.items():
        if content[: len(signature)] == signature:
            return declared_type in valid_types
    return False


def _resolve_corpus_for_ingest(
    request: Request,
    corpus_id_form: UUID | None,
) -> tuple[UUID, str]:
    """Resolve corpus ID and Qdrant collection for ingestion.

    Returns:
        Tuple of (corpus_id, qdrant_collection).
        If admin/tenant key, qdrant_collection is "" (caller must look up from DB).
    """
    auth_corpus: Corpus | None = getattr(request.state, "corpus", None)
    is_admin: bool = getattr(request.state, "is_admin", False)
    tenant = getattr(request.state, "tenant", None)

    # Corpus key auth
    if auth_corpus is not None:
        if corpus_id_form is not None and corpus_id_form != auth_corpus.id:
            raise HTTPException(
                status_code=403,
                detail="corpus_id does not match authenticated corpus",
            )
        return auth_corpus.id, auth_corpus.qdrant_collection

    # Tenant key auth — requires corpus_id, ownership verified after DB lookup
    if tenant is not None:
        if corpus_id_form is None:
            raise HTTPException(
                status_code=422,
                detail="corpus_id is required when using a tenant API key",
            )
        return corpus_id_form, ""

    # Admin key auth
    if is_admin:
        if corpus_id_form is None:
            raise HTTPException(
                status_code=422,
                detail="corpus_id is required when using an admin API key",
            )
        return corpus_id_form, ""

    raise HTTPException(status_code=401, detail="Not authenticated")


def _is_unique_violation(error: IntegrityError) -> bool:
    """A unique-key clash (Postgres SQLSTATE 23505), as opposed to a missing foreign row."""
    orig = error.orig
    return getattr(orig, "sqlstate", None) == "23505" or getattr(orig, "pgcode", None) == "23505"


async def _job_for_key(session: AsyncSession, corpus_id: UUID, key: str) -> IngestJob | None:
    """The upload an idempotency key already names in this corpus (keys are per corpus)."""
    result = await session.execute(
        select(IngestJob).where(IngestJob.corpus_id == corpus_id, IngestJob.idempotency_key == key)
    )
    return result.scalar_one_or_none()


def _replay(job: IngestJob, content_sha256: str) -> IngestResponse:
    """A retry of an upload that was already accepted: the original job, not a new one.

    The same key with different bytes is a client bug, and silently answering with the
    other file's job would lose this one: that is a 409.
    """
    stored = (job.review_metadata or {}).get("content_sha256")
    if stored is not None and stored != content_sha256:
        raise HTTPException(
            status_code=409,
            detail="idempotency_key was already used for a different file in this corpus",
        )
    return IngestResponse(
        job_id=job.id,
        status=job.status,
        message="Already accepted: this is the job the idempotency_key first created",
    )


# BIZ3 records queue
# The dispatch helpers moved to `pipeline/records_queue.py` (articles share them).
_fail_undispatched = fail_undispatched
_QUEUE_DOWN = QUEUE_DOWN
# /BIZ3 records queue


@router.post("", response_model=IngestResponse, status_code=202)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    corpus_id: UUID | None = Form(None),
    idempotency_key: str | None = Form(None),
    mode: str = Form("auto"),
    target_document_id: UUID | None = Form(None),
    auto_confirm: bool = Form(False),
    metadata: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    """Upload a document for ingestion. Processing happens async via Celery.

    `idempotency_key` is scoped to the corpus. Sending the same key again with the same
    file returns the job it first created instead of a new one; the same key with a
    different file is a 409. If the task queue is unavailable the job is marked failed,
    its key released, and the response is a 503.
    """
    resolved_corpus_id, qdrant_collection = _resolve_corpus_for_ingest(request, corpus_id)

    # Look up corpus from DB (validates existence + gets collection if admin/tenant)
    corpus_obj = await session.get(Corpus, resolved_corpus_id)
    if not corpus_obj:
        raise HTTPException(status_code=404, detail="Corpus not found")

    # Tenant ownership check
    tenant = getattr(request.state, "tenant", None)
    if tenant is not None and corpus_obj.tenant_id != tenant.id:
        raise HTTPException(status_code=403, detail="Corpus does not belong to this tenant")
    # COB9 platform docs (start)
    allow_system_write(request, corpus_obj)  # a super admin adds platform docs
    if is_system_corpus(corpus_obj):
        # Nobody can confirm a pending review of a platform page, and a name match could
        # version a built-in page: always a new, confirmed document.
        auto_confirm = True
        if mode == "auto":
            mode = "new"
    # COB9 platform docs (end)

    if not qdrant_collection:
        qdrant_collection = corpus_obj.qdrant_collection

    # Resolve MIME type — browsers send application/octet-stream for .md, .txt, etc.
    resolved_mime = file.content_type
    if not resolved_mime or resolved_mime == "application/octet-stream":
        ext = (
            "." + (file.filename or "").rsplit(".", 1)[-1].lower()
            if file.filename and "." in file.filename
            else ""
        )
        resolved_mime = EXTENSION_MIME_TYPES.get(ext, resolved_mime or "application/octet-stream")

    # Validate file type. Audio is transcribed first, so it is accepted only when a
    # transcription provider is configured — otherwise it would fail in the worker.
    if resolved_mime in AUDIO_MIME_TYPES:
        if request.app.state.settings.transcription_provider == "off":
            raise HTTPException(
                status_code=400,
                detail=(
                    "Audio needs transcription: set RAG_TRANSCRIPTION_PROVIDER "
                    "(whisper_api or whisper_local) to ingest audio files."
                ),
            )
    elif resolved_mime not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {resolved_mime}",
        )

    # Validate mode
    valid_modes = {"auto", "replace", "new", "version"}
    if mode not in valid_modes:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid mode '{mode}'. Must be one of: {', '.join(sorted(valid_modes))}",
        )

    # Parse metadata early (before file upload + DB commit to avoid orphaned jobs)
    import json as _json

    doc_metadata: dict[str, str] = {}
    if metadata:
        try:
            doc_metadata = _json.loads(metadata)
            if not isinstance(doc_metadata, dict):
                raise HTTPException(status_code=422, detail="metadata must be a JSON object")
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Invalid metadata JSON: {e}") from e

    # COB9 platform docs (start)
    if is_system_corpus(corpus_obj):  # audience, source, author
        doc_metadata = platform_metadata(request, doc_metadata)
    # COB9 platform docs (end)

    contents = await file.read()
    byte_limit = await upload_byte_limit(
        session, corpus_obj.tenant_id, request.app.state.settings.max_upload_bytes
    )
    if len(contents) > byte_limit:
        raise APIError(
            413,
            f"File too large: {len(contents):,} bytes; the limit is {byte_limit:,} bytes",
            "upload_too_large",
            limit_bytes=byte_limit,
            size_bytes=len(contents),
        )

    if resolved_mime and not _validate_content_type(contents, resolved_mime):
        raise HTTPException(
            status_code=400,
            detail=f"File content does not match declared type: {resolved_mime}",
        )

    # An idempotency key is scoped to its corpus: another corpus's key is neither a
    # clash nor visible here. A retry of an accepted upload gets its original job.
    content_sha256 = hashlib.sha256(contents).hexdigest()
    if idempotency_key:
        existing = await _job_for_key(session, resolved_corpus_id, idempotency_key)
        if existing is not None:
            return _replay(existing, content_sha256)

    # The tenant's active-document limit (accounts A6), after the replay above (a retry
    # of an accepted upload is not a new one). A replace or new version of an existing
    # document of this corpus adds none; anything else may add one. With a limit, the
    # check holds the tenant's lock until the job below is committed.
    if mode in ("replace", "version"):
        await refuse_system_target(session, resolved_corpus_id, target_document_id)

    async def _upload_adds() -> int:
        if mode in ("replace", "version") and target_document_id is not None:
            from callobuzz_rag.db.models.document import Document

            found = await session.execute(
                select(Document.id).where(
                    Document.id == target_document_id, Document.corpus_id == resolved_corpus_id
                )
            )
            return 0 if found.first() is not None else 1
        return 1

    pending = await check_document_room(session, corpus_obj.tenant_id, _upload_adds)

    # PIP3 uploads: files per day and total storage (a file is a person in the dashboard or an API
    # client, so a clear error is right here; records and website pages are not uploads). After
    # the replay above, like the document limit: a retry of an accepted upload gets its job back,
    # not a 429. Under the tenant's lock, so parallel uploads cannot all read the same numbers.
    try:
        await check_upload_room(
            session,
            corpus_obj.tenant_id,
            len(contents),
            enabled=bool(getattr(request.app.state.settings, "fair_use_enabled", True)),
        )
    except APIError:
        alerts.schedule_upload_alerts(request.app, corpus_obj.tenant_id)  # just reached
        raise
    # /PIP3 uploads

    settings = request.app.state.settings
    storage = object_storage(settings)
    upload_id = uuid_mod.uuid4()
    storage_key = build_raw_document_key(resolved_corpus_id, upload_id)

    # The job row goes in (flushed) BEFORE the file: its foreign key share-locks the
    # corpus row, so a concurrent corpus delete waits for this request and then removes
    # the file with the corpus's others; a delete that already committed fails the
    # flush before anything is stored. The file never outlives its row.
    job = IngestJob(
        status="queued",
        total_documents=1,
        idempotency_key=idempotency_key,
        corpus_id=resolved_corpus_id,
        mode=mode,
        target_document_id=target_document_id,
        storage_key=storage_key,
        pending_new_documents=pending,
        review_metadata={
            "file_name": file.filename or "unknown",
            "mime_type": resolved_mime,
            "content_sha256": content_sha256,
            # What the storage limit counts while the worker has not written the document yet.
            "size_bytes": len(contents),
            # Kept so a retry runs exactly like this upload (tags, and no pending review).
            "doc_metadata": doc_metadata or None,
            "auto_confirm": auto_confirm,
        },
        expires_at=datetime.now(UTC) + timedelta(days=settings.review_expiry_days),
    )
    session.add(job)
    try:
        # Flushed here, inside the guard: any query before it would autoflush the job outside
        # it, and a concurrent retry's key clash would escape as a 500 instead of its job.
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        if _is_unique_violation(e) and idempotency_key:
            # A concurrent request with the same key won the insert; it has committed.
            existing = await _job_for_key(session, resolved_corpus_id, idempotency_key)
            if existing is not None:
                return _replay(existing, content_sha256)
            raise HTTPException(
                status_code=409, detail="idempotency_key is in use by an upload in progress"
            ) from e
        raise HTTPException(status_code=404, detail="Corpus not found") from e
    # PIP3 uploads: counted in the same transaction as the job, so a refused upload is not
    await _count_upload(session, corpus_obj.tenant_id)
    # /PIP3 uploads
    try:
        await storage.upload(
            settings.s3_bucket,
            storage_key,
            io.BytesIO(contents),
            content_type=resolved_mime,
        )
    except Exception as e:
        await session.rollback()
        logger.error("Upload of %s to object storage failed: %s", storage_key, e, exc_info=True)
        raise HTTPException(
            status_code=503, detail="Object storage is unavailable; nothing was stored"
        ) from e
    try:
        await session.commit()
    except SQLAlchemyError as e:
        await session.rollback()
        await discard_object(storage, settings.s3_bucket, storage_key, reason="upload not saved")
        if isinstance(e, IntegrityError):
            raise HTTPException(status_code=404, detail="Corpus not found") from e
        raise HTTPException(status_code=503, detail="The upload could not be saved") from e
    await session.refresh(job)

    from callobuzz_rag.worker.tasks import ingest_document_task

    try:
        ingest_document_task.delay(
            storage_key=storage_key,
            file_name=file.filename or "unknown",
            mime_type=resolved_mime,
            corpus_id=str(resolved_corpus_id),
            job_id=str(job.id),
            qdrant_collection=qdrant_collection,
            mode=mode,
            target_document_id=str(target_document_id) if target_document_id else None,
            auto_confirm=auto_confirm,
            doc_metadata=doc_metadata if doc_metadata else None,
        )
    except Exception as e:
        await _fail_undispatched(session, job, e)
        await discard_object(storage, settings.s3_bucket, storage_key, reason="never queued")
        raise HTTPException(status_code=503, detail=_QUEUE_DOWN) from e

    alerts.schedule_upload_alerts(request.app, corpus_obj.tenant_id)  # PIP3 uploads: 80 %, 100 %
    return IngestResponse(job_id=job.id, status="queued")


async def _count_upload(session: AsyncSession, tenant_id: UUID) -> None:  # PIP3 uploads
    """Add one to the business's uploads of today (a plain upsert in the upload's transaction)."""
    from callobuzz_rag.fairuse.counters import bump

    await bump(session, tenant_id, "uploads", 1)


@router.post("/records", response_model=IngestRecordsResponse, status_code=202)
async def ingest_records(
    request: Request,
    body: IngestRecordsRequest,
    session: AsyncSession = Depends(get_session),
) -> IngestRecordsResponse:
    """Import up to 1000 text records (FAQ entries, product listings, help articles).

    No file parsing: each record is chunked, embedded and indexed directly, in one
    batch. Records are keyed by `id` — importing an id again replaces that record, and
    an unchanged record is skipped, so re-syncing a whole catalogue is cheap.
    """
    resolved_corpus_id, qdrant_collection = _resolve_corpus_for_ingest(request, body.corpus_id)
    corpus_obj = await session.get(Corpus, resolved_corpus_id)
    if not corpus_obj:
        raise HTTPException(status_code=404, detail="Corpus not found")
    tenant = getattr(request.state, "tenant", None)
    if tenant is not None and corpus_obj.tenant_id != tenant.id:
        raise HTTPException(status_code=403, detail="Corpus does not belong to this tenant")
    # COB9 platform docs (start)
    allow_system_write(request, corpus_obj)  # a super admin adds platform docs
    if is_system_corpus(corpus_obj):
        for record in body.records:
            record.metadata = platform_metadata(request, record.metadata)
    # COB9 platform docs (end)
    qdrant_collection = qdrant_collection or corpus_obj.qdrant_collection

    # BIZ3 records queue
    job = await queue_records(
        session,
        corpus_obj,
        body.records,
        qdrant_collection=qdrant_collection,
        allow_system=is_system_corpus(corpus_obj),
        room_check=check_document_room,
    )
    # /BIZ3 records queue
    return IngestRecordsResponse(job_id=job.id, status="queued", records=len(body.records))


async def _job_responses(session: AsyncSession, jobs: list[IngestJob]) -> list[JobStatusResponse]:
    """Jobs as responses, with the file names of the documents a review resembles."""
    from callobuzz_rag.db.models.document import Document

    similar_ids: set[UUID] = set()
    for job in jobs:
        for doc in job.similar_documents or []:
            try:
                similar_ids.add(UUID(str(doc.get("document_id"))))
            except (TypeError, ValueError):
                continue
    names: dict[UUID, str | None] = {}
    if similar_ids:
        rows = await session.execute(
            select(Document.id, Document.file_name).where(Document.id.in_(similar_ids))
        )
        names = {row[0]: row[1] for row in rows.all()}

    failed_ids = [str(job.id) for job in jobs if job.status == "failed"]
    retried: set[str] = set()
    if failed_ids:
        of = IngestJob.review_metadata["retry_of"].as_string()
        rows = await session.execute(
            select(of).where(of.in_(failed_ids), IngestJob.status != "failed").distinct()
        )
        retried = {str(row[0]) for row in rows.all()}

    out: list[JobStatusResponse] = []
    for job in jobs:
        response = JobStatusResponse.model_validate(job)
        response.retried = str(job.id) in retried
        if job.similar_documents:
            response.similar_documents = [
                SimilarDocumentInfo(
                    document_id=doc["document_id"],
                    similarity=doc.get("similarity", 0.0),
                    match_count=doc.get("match_count", 0),
                    file_name=names.get(UUID(str(doc["document_id"]))),
                )
                for doc in job.similar_documents
                if doc.get("document_id")
            ]
        out.append(response)
    return out


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    request: Request,
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> JobStatusResponse:
    """One ingestion job: status, corpus, file, mode and, for a review, similar documents."""
    job = await session.get(IngestJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    allowed = await authorized_corpus_ids(resolve_scope(request), None, session)
    if allowed is not None and job.corpus_id not in allowed:
        raise HTTPException(status_code=403, detail="Job is outside this key's scope")

    return (await _job_responses(session, [job]))[0]


_JOB_STATUSES = {s.value for s in JobStatus}


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    request: Request,
    status: str | None = Query(None, description="Only jobs in this status."),
    corpus_id: UUID | None = Query(None, description="Only this corpus's jobs."),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> JobListResponse:
    """List ingestion jobs, newest first: admin all, tenant its corpora, corpus key its own."""
    if status is not None and status not in _JOB_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown status '{status}'. One of: {', '.join(sorted(_JOB_STATUSES))}",
        )
    allowed = await authorized_corpus_ids(resolve_scope(request), corpus_id, session)

    where: list[ColumnElement[bool]] = []
    if allowed is not None:
        where.append(IngestJob.corpus_id.in_(allowed))
    elif corpus_id is None:
        # The admin's all-jobs list leaves out COB's own docs imports (one job per 50 records).
        where.append(
            IngestJob.corpus_id.not_in(select(Corpus.id).where(Corpus.is_system.is_(True)))
        )
    if status is not None:
        where.append(IngestJob.status == status)

    total = (await session.execute(select(func.count(IngestJob.id)).where(*where))).scalar() or 0
    result = await session.execute(
        select(IngestJob)
        .where(*where)
        .order_by(IngestJob.created_at.desc(), IngestJob.id)
        .offset(offset)
        .limit(limit)
    )
    jobs = list(result.scalars().all())
    return JobListResponse(
        items=await _job_responses(session, jobs),
        count=len(jobs),
        total=total,
        offset=offset,
        limit=limit,
    )


def _not_retryable(reason: str, detail: str) -> APIError:
    return APIError(409, detail, "job_not_retryable", reason=reason)


@router.post("/jobs/{job_id}/retry", response_model=IngestResponse, status_code=202)
async def retry_job(
    request: Request,
    job_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    """Run a failed upload again from its stored file, as a new job (the failed one stays).

    Only an upload job in `failed` whose stored file still exists qualifies; anything else is
    409 `job_not_retryable` with a `reason`: `records_job` (a records import has no file),
    `not_failed`, `source_unknown` (an old job that did not keep its file name or type) or
    `file_missing` or `already_retried` (a retry of it is already running or finished; see its new
    job). The tenant's document limit applies like for a new upload.
    """
    result = await session.execute(
        select(IngestJob).where(IngestJob.id == job_id).with_for_update()
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    allowed = await authorized_corpus_ids(resolve_scope(request), None, session)
    if allowed is not None and job.corpus_id not in allowed:
        raise HTTPException(status_code=403, detail="Job is outside this key's scope")

    if job.mode == "records":
        raise _not_retryable("records_job", "A records import has no file to run again.")
    if job.status != "failed":
        raise _not_retryable("not_failed", "Only a failed upload can be retried.")
    meta = job.review_metadata or {}
    file_name, mime_type = meta.get("file_name"), meta.get("mime_type")
    if not job.storage_key or not file_name or not mime_type or job.corpus_id is None:
        raise _not_retryable(
            "source_unknown", "This upload did not keep its file details, so it cannot be retried."
        )
    # The job row is locked above, so two clicks at once cannot both pass this check.
    live = await session.execute(
        select(IngestJob.id)
        .where(
            IngestJob.review_metadata["retry_of"].as_string() == str(job.id),
            IngestJob.status != "failed",
        )
        .limit(1)
    )
    if live.first() is not None:
        raise _not_retryable(
            "already_retried", "This upload is already running again; see its new job."
        )
    corpus = await session.get(Corpus, job.corpus_id)
    if corpus is None:
        raise HTTPException(status_code=404, detail="Corpus not found")
    allow_system_write(request, corpus)  # COB9 platform docs: a super admin retries one

    settings = request.app.state.settings
    storage = object_storage(settings)
    if not await storage.exists(settings.s3_bucket, job.storage_key):
        raise _not_retryable("file_missing", "The stored file is gone, so please upload it again.")

    # Like a new upload, except a replace or new version of a known document adds none.
    adds = 0 if job.mode in ("replace", "version") and job.target_document_id else 1
    pending = await check_document_room(session, corpus.tenant_id, adds)

    retry = IngestJob(
        status="queued",
        total_documents=1,
        corpus_id=job.corpus_id,
        mode=job.mode,
        target_document_id=job.target_document_id,
        storage_key=job.storage_key,
        pending_new_documents=pending,
        review_metadata={**meta, "retry_of": str(job.id)},
        expires_at=datetime.now(UTC) + timedelta(days=settings.review_expiry_days),
    )
    session.add(retry)
    await session.commit()
    await session.refresh(retry)

    from callobuzz_rag.worker.tasks import ingest_document_task

    try:
        ingest_document_task.delay(
            storage_key=job.storage_key,
            file_name=file_name,
            mime_type=mime_type,
            corpus_id=str(job.corpus_id),
            job_id=str(retry.id),
            qdrant_collection=corpus.qdrant_collection,
            mode=job.mode,
            target_document_id=str(job.target_document_id) if job.target_document_id else None,
            auto_confirm=bool(meta.get("auto_confirm", False)),
            doc_metadata=meta.get("doc_metadata") or None,
        )
    except Exception as e:
        # The file belongs to the failed job too: only the new job is marked failed.
        await _fail_undispatched(session, retry, e)
        raise HTTPException(status_code=503, detail=_QUEUE_DOWN) from e
    return IngestResponse(job_id=retry.id, status="queued")


_SKIPPED_MESSAGE = "Skipped: nothing was added and the upload was removed"


@router.post("/{job_id}/confirm", response_model=IngestResponse)
async def confirm_pending_review(
    request: Request,
    job_id: UUID,
    body: ConfirmReviewRequest,
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    """Confirm a pending_review ingest job.

    After uploading with auto mode and getting pending_review status,
    call this endpoint to decide: replace an existing document, proceed as a
    new document, or skip (`skip`, alias `discard`): the upload is dropped, nothing is
    indexed and its stored file is deleted. Skipping twice returns the same answer.
    """
    result = await session.execute(
        select(IngestJob).where(IngestJob.id == job_id).with_for_update()
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Auth check — before anything that reveals or changes the job's state
    auth_corpus: Corpus | None = getattr(request.state, "corpus", None)
    if auth_corpus and job.corpus_id and job.corpus_id != auth_corpus.id:
        raise HTTPException(status_code=403, detail="Job belongs to a different corpus")
    tenant = getattr(request.state, "tenant", None)
    if tenant is not None:
        job_corpus = await session.get(Corpus, job.corpus_id) if job.corpus_id else None
        if job_corpus is None or job_corpus.tenant_id != tenant.id:
            raise HTTPException(status_code=403, detail="Job does not belong to this tenant")

    if body.action == "skip" and job.status == "skipped":
        return IngestResponse(job_id=job.id, status="skipped", message=_SKIPPED_MESSAGE)

    if job.status != "pending_review":
        raise HTTPException(
            status_code=409,
            detail=f"Job is not pending review (current status: {job.status})",
        )

    # Check expiry
    if job.expires_at and job.expires_at < datetime.now(UTC):
        job.status = "expired"
        await session.commit()
        raise HTTPException(status_code=410, detail="Review period has expired")

    if body.action == "skip":
        # A job pending review is not queued, so it holds no document-cap reservation;
        # clear the field anyway so a stale value can never count against the tenant.
        storage_key = job.storage_key
        job.status = JobStatus.SKIPPED
        job.finished_at = datetime.now(UTC)
        job.pending_new_documents = 0
        job.storage_key = None
        await session.commit()
        if storage_key:
            settings = request.app.state.settings
            await discard_object(
                object_storage(settings),
                settings.s3_bucket,
                storage_key,
                reason="upload skipped by its owner",
            )
        return IngestResponse(job_id=job.id, status="skipped", message=_SKIPPED_MESSAGE)

    # Determine mode based on user's action
    if body.action == "replace":
        confirmed_mode = "replace"
        target_doc_id = body.target_document_id
        if not target_doc_id:
            raise HTTPException(
                status_code=422,
                detail="target_document_id required when action is 'replace'",
            )
        if job.corpus_id is not None:
            await refuse_system_target(session, job.corpus_id, target_doc_id)
    else:
        confirmed_mode = "new"
        target_doc_id = None

    # Active-document limit (accounts A6): "new" adds a document, "replace" does not. A
    # job pending review is not in flight, so it is not counted until confirmed here.
    pending = 1 if confirmed_mode == "new" else 0
    if confirmed_mode == "new" and job.corpus_id is not None:
        confirm_corpus = await session.get(Corpus, job.corpus_id)
        if confirm_corpus is not None:
            await check_document_room(session, confirm_corpus.tenant_id, 1)

    # Update job and dispatch Celery task
    previous_mode, previous_target = job.mode, job.target_document_id
    previous_pending = job.pending_new_documents
    job.status = "queued"
    job.mode = confirmed_mode
    job.target_document_id = target_doc_id
    job.pending_new_documents = pending
    await session.commit()

    storage_key = job.storage_key
    if not storage_key:
        raise HTTPException(status_code=500, detail="Storage key not found for job")

    from callobuzz_rag.worker.tasks import ingest_document_task

    # Get corpus info for task dispatch
    corpus_obj = await session.get(Corpus, job.corpus_id)
    qdrant_collection = corpus_obj.qdrant_collection if corpus_obj else "chunks"

    file_name = (job.review_metadata or {}).get("file_name", "unknown")
    mime_type = (job.review_metadata or {}).get("mime_type", "application/octet-stream")

    try:
        ingest_document_task.delay(
            storage_key=storage_key,
            file_name=file_name,
            mime_type=mime_type,
            corpus_id=str(job.corpus_id),
            job_id=str(job.id),
            qdrant_collection=qdrant_collection,
            mode=confirmed_mode,
            target_document_id=str(target_doc_id) if target_doc_id else None,
            auto_confirm=True,
        )
    except Exception as e:
        # Still reviewable: back to pending_review, so the confirmation can be retried.
        logger.error("Dispatching confirmed job %s failed: %s", job.id, e, exc_info=True)
        job.status = "pending_review"
        job.mode = previous_mode
        job.target_document_id = previous_target
        job.pending_new_documents = previous_pending
        await session.commit()
        raise HTTPException(
            status_code=503,
            detail="The task queue is unavailable; the job is still pending review. Retry later.",
        ) from e

    return IngestResponse(
        job_id=job.id,
        status="queued",
        message=f"Confirmed: proceeding as '{confirmed_mode}'",
    )
