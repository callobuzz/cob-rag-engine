"""Review workflow routes — submit, approve, reject, ingest, audit trail."""

import logging
import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, get_settings
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.content_portal.models.workspace import Workspace
from callobuzz_rag.content_portal.routes._access import (
    authorize_workspace,
    portal_actor,
    scoped_workspace,
)
from callobuzz_rag.content_portal.schemas.review import (
    ApproveWorkspaceRequest,
    AuditTrailEntry,
    AuditTrailResponse,
    IngestWorkspaceRequest,
    IngestWorkspaceResponse,
    RejectWorkspaceRequest,
    SubmitForReviewRequest,
    SubmitForReviewResponse,
    WorkspaceStatusResponse,
)
from callobuzz_rag.content_portal.services.review_workflow import (
    ChunkValidationError,
    IngestStorageError,
    InvalidTransitionError,
    MakerCheckerViolationError,
    ReviewWorkflowError,
    ReviewWorkflowService,
    WrongWorkspaceKindError,
)
from callobuzz_rag.core.config import Settings
from callobuzz_rag.fairuse import alerts  # PIP3 uploads

if TYPE_CHECKING:
    from callobuzz_rag.storage.base import ObjectStorageProvider

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspaces",
    tags=["portal-review"],
    dependencies=[Depends(authorize_workspace)],
)

_service = ReviewWorkflowService()


def _storage(settings: Settings) -> "ObjectStorageProvider":
    from callobuzz_rag.storage.factory import object_storage

    return object_storage(settings)


def _handle_workflow_error(exc: Exception) -> HTTPException:
    """Map workflow exceptions to HTTP status codes."""
    if isinstance(exc, WrongWorkspaceKindError):
        return APIError(409, str(exc), "wrong_workspace_kind")
    if isinstance(exc, IngestStorageError):
        logger.error("Portal ingest upload failed: %s", exc, exc_info=True)
        return HTTPException(
            status_code=503, detail="Object storage is unavailable; nothing was stored"
        )
    if isinstance(exc, MakerCheckerViolationError):
        # No catalog code fits (accounts README): a plain 403 with the rule in the detail.
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ChunkValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, InvalidTransitionError):
        return APIError(409, str(exc), "invalid_status_transition")
    if isinstance(exc, ReviewWorkflowError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=500, detail="Internal server error")


@router.post(
    "/{workspace_id}/submit",
    response_model=SubmitForReviewResponse,
)
async def submit_for_review(
    workspace_id: uuid.UUID,
    request: Request,
    body: SubmitForReviewRequest | None = None,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(scoped_workspace),
) -> SubmitForReviewResponse:
    """Submit a workspace for review."""
    body = body or SubmitForReviewRequest()
    try:
        result = await _service.submit_for_review(
            session=session,
            workspace_id=workspace_id,
            user=portal_actor(request, body.user),
        )
    except ReviewWorkflowError as e:
        raise _handle_workflow_error(e) from e

    await session.commit()
    return SubmitForReviewResponse(**result)


@router.post(
    "/{workspace_id}/approve",
    response_model=WorkspaceStatusResponse,
)
async def approve_workspace(
    workspace_id: uuid.UUID,
    request: Request,
    body: ApproveWorkspaceRequest | None = None,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(scoped_workspace),
) -> WorkspaceStatusResponse:
    """Approve a workspace that is in review."""
    body = body or ApproveWorkspaceRequest()
    try:
        result = await _service.approve_workspace(
            session=session,
            workspace_id=workspace_id,
            user=portal_actor(request, body.user),
            comment=body.comment,
        )
    except ReviewWorkflowError as e:
        raise _handle_workflow_error(e) from e

    await session.commit()
    return WorkspaceStatusResponse(**result)


@router.post(
    "/{workspace_id}/reject",
    response_model=WorkspaceStatusResponse,
)
async def reject_workspace(
    workspace_id: uuid.UUID,
    body: RejectWorkspaceRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(scoped_workspace),
) -> WorkspaceStatusResponse:
    """Reject a workspace and return it to draft."""
    try:
        result = await _service.reject_workspace(
            session=session,
            workspace_id=workspace_id,
            user=portal_actor(request, body.user),
            feedback=body.feedback,
        )
    except ReviewWorkflowError as e:
        raise _handle_workflow_error(e) from e

    await session.commit()
    return WorkspaceStatusResponse(**result)


@router.post(
    "/{workspace_id}/ingest",
    response_model=IngestWorkspaceResponse,
)
async def ingest_workspace(
    workspace_id: uuid.UUID,
    request: Request,
    body: IngestWorkspaceRequest | None = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    workspace: Workspace = Depends(scoped_workspace),
) -> IngestWorkspaceResponse:
    """Trigger ingestion of an approved workspace.

    The workspace is packaged as one new document, so the tenant needs room for one more
    active document (409 `document_limit_reached`; accounts A6).
    """
    body = body or IngestWorkspaceRequest()
    from callobuzz_rag.accounts.caps import check_document_room
    from callobuzz_rag.db.models.corpus import Corpus

    corpus = await session.get(Corpus, workspace.corpus_id)
    if corpus is not None:
        await check_document_room(session, corpus.tenant_id, 1)
    try:
        result = await _service.ingest_workspace(
            session=session,
            workspace_id=workspace_id,
            user=portal_actor(request, body.user),
            settings=settings,
        )
    except ReviewWorkflowError as e:
        raise _handle_workflow_error(e) from e
    except APIError:  # PIP3 uploads: the day's uploads or the storage are used
        if corpus is not None:
            alerts.schedule_upload_alerts(request.app, corpus.tenant_id)
        raise

    dispatch = result.pop("dispatch")
    try:
        await session.commit()
    except SQLAlchemyError as e:
        # The packaged file is stored but its job row is not: take the file back out.
        await session.rollback()
        from callobuzz_rag.storage.cleanup import discard_object

        await discard_object(
            _storage(settings),
            settings.s3_bucket,
            dispatch["storage_key"],
            reason="portal ingest not saved",
        )
        if isinstance(e, IntegrityError):
            raise HTTPException(status_code=404, detail="Corpus not found") from e
        raise HTTPException(status_code=503, detail="The ingest could not be saved") from e

    # Only now: the worker must find the committed job row.
    from callobuzz_rag.worker.tasks import ingest_document_task

    ingest_document_task.delay(**dispatch)
    if corpus is not None:  # PIP3 uploads: the 80 % and 100 % notices
        alerts.schedule_upload_alerts(request.app, corpus.tenant_id)
    return IngestWorkspaceResponse(**result)


@router.get(
    "/{workspace_id}/audit-trail",
    response_model=AuditTrailResponse,
)
async def get_audit_trail(
    workspace_id: uuid.UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(scoped_workspace),
) -> AuditTrailResponse:
    """Get the audit trail for a workspace."""
    try:
        entries, total = await _service.get_audit_trail(
            session=session,
            workspace_id=workspace_id,
            offset=offset,
            limit=limit,
        )
    except ReviewWorkflowError as e:
        raise _handle_workflow_error(e) from e

    return AuditTrailResponse(
        workspace_id=workspace_id,
        entries=[AuditTrailEntry(**e) for e in entries],
        total=total,
    )
