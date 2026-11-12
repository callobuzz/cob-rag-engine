"""Content staging routes — upload raw content into a workspace.

Any key whose scope reaches the workspace's corpus may use them (`scoped_workspace`).
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, get_settings
from callobuzz_rag.content_portal.models.workspace import Workspace
from callobuzz_rag.content_portal.routes._access import authorize_workspace, scoped_workspace
from callobuzz_rag.content_portal.schemas.staging import (
    ContentUploadRequest,
    ReprocessRequest,
    ReprocessResponse,
    StagedContentListResponse,
    StagedContentResponse,
)
from callobuzz_rag.content_portal.services.staging_service import (
    StagedContentNotFoundError,
    StagingError,
    StagingService,
    StagingStorageError,
    WorkspaceGoneError,
)
from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspaces",
    tags=["portal-staging"],
    dependencies=[Depends(authorize_workspace)],
)


@router.post(
    "/{workspace_id}/content",
    response_model=StagedContentResponse,
    status_code=201,
)
async def upload_content(
    workspace_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    workspace: Workspace = Depends(scoped_workspace),
) -> StagedContentResponse:
    """Upload raw content to a workspace.

    Supports three source types:
    - **file**: Upload via multipart form (file field)
    - **text_dump**: Send raw text in JSON body
    - **url**: Send a URL in JSON body (AI transformer fetches it)

    After upload, an AI transformation task is dispatched automatically.
    """
    workspace_corpus_id = workspace.corpus_id

    svc = StagingService(session, settings)

    content_type = request.headers.get("content-type", "")

    try:
        if "multipart/form-data" in content_type:
            # File upload via multipart
            form = await request.form()
            file = form.get("file")
            if file is not None and hasattr(file, "filename") and file.filename:
                staged = await svc.upload_file(
                    workspace_id,
                    workspace_corpus_id,
                    file,  # type: ignore[arg-type]
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Multipart request must include a 'file' field",
                )
        elif "application/json" in content_type or not content_type:
            # JSON body for text/URL uploads
            raw = await request.json()
            try:
                body = ContentUploadRequest(**raw)
            except Exception as e:
                raise HTTPException(status_code=422, detail=str(e)) from e

            if body.source_type == "text_dump":
                staged = await svc.upload_text(
                    workspace_id,
                    workspace_corpus_id,
                    body.content,
                    title=body.title,
                )
            elif body.source_type == "url":
                staged = await svc.upload_url(
                    workspace_id,
                    workspace_corpus_id,
                    body.content,
                    title=body.title,
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported source_type: {body.source_type}",
                )
        else:
            raise HTTPException(
                status_code=400,
                detail="Either a file upload (multipart) or a JSON body with source_type is required",
            )
    except WorkspaceGoneError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except StagingStorageError as e:
        await session.rollback()
        logger.error("Staging upload failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=503, detail="Object storage is unavailable; nothing was stored"
        ) from e
    except StagingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        await session.commit()
    except SQLAlchemyError as e:
        # The file is stored but its row is not: take the file back out.
        await session.rollback()
        await svc.discard(staged.raw_storage_key)
        if isinstance(e, IntegrityError):
            raise HTTPException(status_code=404, detail="Workspace not found") from e
        raise HTTPException(status_code=503, detail="The upload could not be saved") from e
    await session.refresh(staged)

    # Dispatch AI transformation (best-effort — record is already committed)
    try:
        svc.dispatch_ai_transform(staged.id, workspace_corpus_id)
    except Exception:
        logger.exception("Failed to dispatch AI transform for content %s", staged.id)

    return StagedContentResponse.model_validate(staged)


@router.get(
    "/{workspace_id}/content",
    response_model=StagedContentListResponse,
)
async def list_content(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    workspace: Workspace = Depends(scoped_workspace),
) -> StagedContentListResponse:
    """List all staged content items in a workspace."""
    svc = StagingService(session, settings)
    items = await svc.list_content(workspace.id)
    return StagedContentListResponse(
        items=[StagedContentResponse.model_validate(item) for item in items],
        count=len(items),
    )


@router.get(
    "/{workspace_id}/content/{content_id}",
    response_model=StagedContentResponse,
)
async def get_content(
    content_id: UUID,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    workspace: Workspace = Depends(scoped_workspace),
) -> StagedContentResponse:
    """Get a single staged content item with its AI processing status."""
    svc = StagingService(session, settings)
    staged = await svc.get_content(workspace.id, content_id)
    if staged is None:
        raise HTTPException(status_code=404, detail="Staged content not found")

    return StagedContentResponse.model_validate(staged)


@router.post(
    "/{workspace_id}/content/{content_id}/reprocess",
    response_model=ReprocessResponse,
)
async def reprocess_content(
    workspace_id: UUID,
    content_id: UUID,
    body: ReprocessRequest | None = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    workspace: Workspace = Depends(scoped_workspace),
) -> ReprocessResponse:
    """Re-run AI transformation on previously staged content.

    Use `force=true` to reprocess content that already completed successfully.
    Optionally override the LLM model for this reprocessing run.
    """
    workspace_corpus_id = workspace.corpus_id

    force = body.force if body else False
    model_override = body.model_override if body else None

    svc = StagingService(session, settings)

    try:
        staged = await svc.reprocess_content(
            workspace_id, content_id, force=force, model_override=model_override
        )
    except StagedContentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except StagingError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        await session.commit()
    except SQLAlchemyError as e:
        # Nothing new was stored: the content's file belongs to its committed row.
        await session.rollback()
        raise HTTPException(status_code=503, detail="The reprocess could not be saved") from e
    await session.refresh(staged)

    # Dispatch new AI transformation (best-effort — record is already committed)
    try:
        svc.dispatch_ai_transform(staged.id, workspace_corpus_id, model_override)
    except Exception:
        logger.exception("Failed to dispatch AI transform for content %s reprocess", staged.id)

    return ReprocessResponse(
        content_id=staged.id,
        ai_status=staged.ai_status,
        message="AI transformation re-queued",
    )
