"""The operator's view of COB's built-in knowledge base: its sync status and a resync button.

Session-only: an API key, even the admin key, is refused (403 `session_required`), like every
COB route.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.api.deps import get_session, require_admin, require_session_user
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.cob_admin import (
    CobDocumentAudienceRequest,
    CobDocumentList,
    CobDocumentOut,
    CobResyncRequest,
    CobResyncResponse,
    CobReviewItem,
    CobReviewResponse,
    CobStatusOut,
)
from callobuzz_rag.cob.manifest import ManifestError
from callobuzz_rag.cob.platform import PLATFORM, doc_source, refuse_builtin
from callobuzz_rag.cob.sync import STATUS_ID, request_resync
from callobuzz_rag.cob.system import get_system_corpus
from callobuzz_rag.cob.types import DocsIndex
from callobuzz_rag.db.models.cob_status import CobStatus
from callobuzz_rag.db.models.document import Document

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/cob",
    tags=["cob"],
    dependencies=[Depends(require_admin), Depends(require_session_user)],
)

UNAVAILABLE = "unavailable"


def _current_index() -> DocsIndex | None:
    """The docs shipped with this engine, or None when they cannot be read."""
    from callobuzz_rag.cob.kb import load_docs_index

    try:
        return load_docs_index()
    except ManifestError:
        logger.warning("COB docs cannot be read", exc_info=True)
        return None


@router.get("/status", response_model=CobStatusOut, summary="COB docs status")
async def cob_status(session: AsyncSession = Depends(get_session)) -> CobStatusOut:
    """What COB's knowledge base holds, whether it matches the shipped docs, and how the last
    sync went. Platform operator only."""
    row = await session.get(CobStatus, STATUS_ID)
    index = _current_index()
    current = index.version if index is not None else UNAVAILABLE
    corpus = await get_system_corpus(session)
    return CobStatusOut(
        docs_version=row.docs_version if row else None,
        current_version=current,
        up_to_date=bool(row and row.docs_version and row.docs_version == current),
        sync_state=row.sync_state if row else "idle",
        started_at=row.started_at if row else None,
        synced_at=row.synced_at if row else None,
        document_count=row.document_count if row else 0,
        last_error=row.last_error if row else None,
        eval=row.eval if row else None,
        corpus_id=corpus.id if corpus else None,
    )


@router.post(
    "/resync", response_model=CobResyncResponse, status_code=202, summary="Resync COB docs"
)
async def cob_resync(
    body: CobResyncRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> CobResyncResponse:
    """Queue a forced re-read of the docs. Does nothing (`queued: false`) while a sync is
    already queued or running. Platform operator only."""
    index = _current_index()
    if index is None:
        raise APIError(
            503, "The documentation files cannot be read on this engine.", "cob_docs_unavailable"
        )
    row = await session.get(CobStatus, STATUS_ID)
    from_version = row.docs_version if row else None
    queued = await request_resync(session, index, now=datetime.now(UTC))
    if not queued:
        await session.commit()
        return CobResyncResponse(queued=False)
    await record_audit(
        session,
        request,
        "cob.resync_requested",
        target_type="cob",
        target_id="docs",
        details={"eval": body.eval, "from_version": from_version},
    )
    await session.commit()
    from callobuzz_rag.worker.cob_tasks import cob_sync_task

    try:
        cob_sync_task.delay(eval=body.eval, force=True)
    except Exception as exc:
        logger.error("COB resync could not be queued", exc_info=True)
        row = await session.get(CobStatus, STATUS_ID)
        if row is not None:
            row.sync_state = "failed"
            row.last_error = "The background worker could not be reached. Try again."
            row.updated_at = datetime.now(UTC)
            await session.commit()
        raise APIError(
            503, "The background worker could not be reached. Try again.", "queue_unavailable"
        ) from exc
    return CobResyncResponse(queued=True)


# COB7 review list (start)
@router.get("/review", response_model=CobReviewResponse, summary="COB answers to review")
async def cob_review(
    days: int = Query(30, ge=1, le=90, description="How many days back to look"),
    rating: Literal["down", "all"] = Query(
        "all", description="`down`: only answers a person rated down"
    ),
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> CobReviewResponse:
    """COB's weak answers, grouped by question: no answer found, statements removed by the fact
    check, thumbs down with the person's comment. Questions are masked. Platform operator only."""
    from callobuzz_rag.cob.eval.review import gather_signals, group_signals

    corpus = await get_system_corpus(session)
    if corpus is None:
        return CobReviewResponse(items=[])
    since = datetime.now(UTC) - timedelta(days=days)
    signals = await gather_signals(session, corpus.id, since)
    groups = group_signals(signals, only_down=rating == "down", limit=limit)
    return CobReviewResponse(
        items=[
            CobReviewItem(
                group=g.group,
                question=g.question,
                count=g.count,
                last_at=g.last_at,
                role=g.role,
                language=g.language,
                top_slug=g.top_slug,
                reasons=g.reasons,
                comments=g.comments,
            )
            for g in groups
        ]
    )


# COB7 review list (end)


# COB9 platform docs (start)
def _document_out(doc: Document) -> CobDocumentOut:
    meta = doc.doc_metadata or {}
    uploaded_by = meta.get("uploaded_by")
    try:
        author = uuid.UUID(str(uploaded_by)) if uploaded_by else None
    except ValueError:
        author = None
    source = doc_source(doc.external_id, meta)
    audience = meta.get("audience") or ("customer" if source == PLATFORM else "public")
    return CobDocumentOut(
        id=doc.id,
        name=doc.file_name or doc.external_id or str(doc.id),
        source=source,
        audience=audience,
        uploaded_by=author,
        ingested_at=doc.ingested_at,
    )


@router.get("/documents", response_model=CobDocumentList, summary="COB's documents")
async def cob_documents(
    source: Literal["all", "builtin", "platform"] = Query("all"),
    session: AsyncSession = Depends(get_session),
) -> CobDocumentList:
    """The documents COB reads: the engine's built-in ones and the operator's platform docs.
    Add a platform doc through the normal upload with COB's knowledge base id. Super admin only."""
    corpus = await get_system_corpus(session)
    if corpus is None:
        return CobDocumentList(items=[])
    rows = (
        (
            await session.execute(
                select(Document)
                .where(Document.corpus_id == corpus.id)
                .order_by(Document.ingested_at.desc())
            )
        )
        .scalars()
        .all()
    )
    items = [_document_out(d) for d in rows]
    if source != "all":
        items = [i for i in items if i.source == source]
    return CobDocumentList(items=items, corpus_id=corpus.id)


@router.patch(
    "/documents/{document_id}",
    response_model=CobDocumentOut,
    summary="Change who COB answers a platform doc to",
)
async def cob_document_audience(
    document_id: uuid.UUID,
    body: CobDocumentAudienceRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> CobDocumentOut:
    """Set the audience of a platform doc (admin, customer or public). Built-in docs are
    refused (403 `cob_builtin_readonly`). Super admin only."""
    corpus = await get_system_corpus(session)
    doc = await session.get(Document, document_id) if corpus is not None else None
    if corpus is None or doc is None or doc.corpus_id != corpus.id:
        raise APIError(404, "That document was not found.", "document_not_found")
    refuse_builtin(doc.external_id, doc.doc_metadata)
    old_audience = (doc.doc_metadata or {}).get("audience") or "customer"
    doc.doc_metadata = {**(doc.doc_metadata or {}), "audience": body.audience}
    await request.app.state.vector_store.update_payload(
        corpus.qdrant_collection,
        {"document_id": str(doc.id)},
        {"meta_audience": body.audience},
    )
    await record_audit(
        session,
        request,
        "cob.platform_doc_audience",
        target_type="document",
        target_id=str(doc.id),
        details={"from": old_audience, "to": body.audience},
    )
    await session.commit()
    return _document_out(doc)


# COB9 platform docs (end)
