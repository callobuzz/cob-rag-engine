"""Workspace management routes — CRUD + lock/unlock.

Every key kind works, scoped like the rest of the engine: create and list name the
corpus with `?corpus_id=` (a corpus key may leave it out), and every route with a
workspace in its path loads it through `scoped_workspace`.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import (
    authorized_corpus_ids,
    get_session,
    get_settings,
    require_owner,
    resolve_scope,
    scoped_corpus,
)
from callobuzz_rag.content_portal.models.workspace import Workspace
from callobuzz_rag.content_portal.routes._access import (
    authorize_workspace,
    portal_actor,
    scoped_workspace,
)
from callobuzz_rag.content_portal.schemas.workspaces import (
    WorkspaceCreate,
    WorkspaceListResponse,
    WorkspaceLock,
    WorkspaceResponse,
    WorkspaceUpdate,
)
from callobuzz_rag.content_portal.services.workspace_service import WorkspaceService
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import WorkspaceLockedError, WorkspaceStateError
from callobuzz_rag.db.models.corpus import Corpus

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspaces",
    tags=["portal-workspaces"],
    dependencies=[Depends(authorize_workspace)],
)

# Shared service instance (stateless — safe to reuse)
_service = WorkspaceService()


@router.post("", response_model=WorkspaceResponse, status_code=201)
async def create_workspace(
    body: WorkspaceCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(scoped_corpus),
) -> WorkspaceResponse:
    """Create a new workspace in a corpus (`?corpus_id=`; a corpus key's own by default)."""
    workspace = await _service.create_workspace(
        session=session,
        corpus_id=corpus.id,
        title=body.title,
        created_by=portal_actor(request),
        workflow_mode=body.workflow_mode,
        assigned_reviewer=body.assigned_reviewer,
    )
    await session.commit()
    await session.refresh(workspace)
    return WorkspaceResponse.model_validate(workspace)


@router.get("", response_model=WorkspaceListResponse)
async def list_workspaces(
    request: Request,
    corpus_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Only this corpus's workspaces. Without it: every corpus in the key's scope "
            "(admin: all, tenant key: its corpora, corpus key: its own)."
        ),
    ),
    status: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceListResponse:
    """List the workspaces of the corpora in the caller's scope."""
    corpus_ids = await authorized_corpus_ids(resolve_scope(request), corpus_id, session)
    items, total = await _service.list_workspaces(
        session=session,
        corpus_ids=corpus_ids,
        status=status,
        offset=offset,
        limit=limit,
    )
    await _service.load_stats_many(session, items)
    return WorkspaceListResponse(
        items=[WorkspaceResponse.model_validate(w) for w in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace: Workspace = Depends(scoped_workspace),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceResponse:
    """Get workspace details by ID, with its content and chunk counts."""
    return WorkspaceResponse.model_validate(await _service.load_stats(session, workspace))


@router.patch("/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    body: WorkspaceUpdate,
    workspace: Workspace = Depends(scoped_workspace),
    session: AsyncSession = Depends(get_session),
) -> WorkspaceResponse:
    """Update workspace title or assigned reviewer."""
    if not body.has_changes():
        raise HTTPException(
            status_code=422,
            detail="No fields to update",
        )

    try:
        workspace = await _service.update_workspace(
            session=session,
            workspace=workspace,
            title=body.title,
            assigned_reviewer=body.assigned_reviewer,
        )
    except WorkspaceStateError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    await session.commit()
    await session.refresh(workspace)
    return WorkspaceResponse.model_validate(workspace)


@router.delete("/{workspace_id}", status_code=204)
async def delete_workspace(
    request: Request,
    workspace: Workspace = Depends(scoped_workspace),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _owner: None = Depends(require_owner),
) -> None:
    """Delete a workspace (only if in draft state), and its staged uploads.

    Owners only (a workspace member's sign-in gets 403 `owner_required`)."""
    prefix = f"portal/{workspace.corpus_id}/{workspace.id}/"  # build_portal_storage_key
    try:
        await _service.delete_workspace(session=session, workspace=workspace)
    except WorkspaceStateError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    await session.commit()

    # After the rows are gone, queued and bounded: a storage blip must neither undo nor
    # hold the delete (`storage/cleanup.py`).
    from callobuzz_rag.storage.cleanup import schedule_prefix_cleanup

    await schedule_prefix_cleanup(
        settings.s3_bucket, [prefix], reason=f"workspace {workspace.id} deleted"
    )


@router.post("/{workspace_id}/lock", response_model=WorkspaceResponse)
async def lock_workspace(
    request: Request,
    workspace: Workspace = Depends(scoped_workspace),
    body: WorkspaceLock | None = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WorkspaceResponse:
    """Acquire an edit lock on a workspace."""
    body = body or WorkspaceLock()
    try:
        workspace = await _service.acquire_lock(
            session=session,
            workspace=workspace,
            user=portal_actor(request, body.user),
            lock_timeout_minutes=settings.portal_lock_timeout_minutes,
        )
    except WorkspaceLockedError as e:
        raise HTTPException(status_code=423, detail=str(e)) from e
    except WorkspaceStateError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    await session.commit()
    await session.refresh(workspace)
    return WorkspaceResponse.model_validate(workspace)


@router.post("/{workspace_id}/unlock", response_model=WorkspaceResponse)
async def unlock_workspace(
    request: Request,
    workspace: Workspace = Depends(scoped_workspace),
    body: WorkspaceLock | None = None,
    session: AsyncSession = Depends(get_session),
) -> WorkspaceResponse:
    """Release an edit lock on a workspace."""
    body = body or WorkspaceLock()
    try:
        workspace = await _service.release_lock(
            session=session,
            workspace=workspace,
            user=portal_actor(request, body.user),
        )
    except WorkspaceLockedError as e:
        raise HTTPException(status_code=423, detail=str(e)) from e

    await session.commit()
    await session.refresh(workspace)
    return WorkspaceResponse.model_validate(workspace)
