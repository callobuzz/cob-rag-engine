"""Chunk editor routes — CRUD, split, merge, reorder, approve/reject."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.content_portal.models.workspace import Workspace
from callobuzz_rag.content_portal.routes._access import (
    authorize_workspace,
    portal_actor,
    scoped_workspace,
)
from callobuzz_rag.content_portal.schemas.chunks import (
    AddChunkRequest,
    ApproveRejectRequest,
    ChunkListResponse,
    ChunkResponse,
    EditChunkRequest,
    MergeChunksRequest,
    MergeChunksResponse,
    ReorderChunksRequest,
    SplitChunkRequest,
    SplitChunkResponse,
)
from callobuzz_rag.content_portal.services.chunk_editor import (
    ChunkEditorError,
    ChunkEditorService,
    ChunkNotFoundError,
    InvalidOperationError,
    ParentChunkDerivedError,
    WorkspaceLockError,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspaces",
    tags=["portal-chunks"],
    dependencies=[Depends(authorize_workspace)],
)

_editor = ChunkEditorService()


def _handle_editor_error(e: ChunkEditorError) -> HTTPException:
    """Map service exceptions to HTTP status codes."""
    if isinstance(e, ParentChunkDerivedError):
        return APIError(409, str(e), ParentChunkDerivedError.code)
    if isinstance(e, ChunkNotFoundError):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, WorkspaceLockError):
        return HTTPException(status_code=409, detail=str(e))
    if isinstance(e, InvalidOperationError):
        return HTTPException(status_code=422, detail=str(e))
    return HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{workspace_id}/chunks",
    response_model=ChunkListResponse,
)
async def list_chunks(
    workspace_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(scoped_workspace),
) -> ChunkListResponse:
    """List chunks in a workspace with pagination."""
    try:
        items, total = await _editor.list_chunks(
            session, workspace_id, offset=offset, limit=limit, status_filter=status
        )
    except ChunkEditorError as e:
        raise _handle_editor_error(e) from e

    return ChunkListResponse(
        items=[ChunkResponse.model_validate(c) for c in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.patch(
    "/{workspace_id}/chunks/{chunk_id}",
    response_model=ChunkResponse,
)
async def edit_chunk(
    workspace_id: UUID,
    chunk_id: UUID,
    body: EditChunkRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(scoped_workspace),
) -> ChunkResponse:
    """Edit a chunk's text and/or metadata."""
    try:
        chunk = await _editor.edit_chunk(
            session,
            workspace_id,
            chunk_id,
            user=portal_actor(request, body.user),
            text=body.text,
            metadata=body.metadata,
        )
        await session.commit()
        await session.refresh(chunk)
    except ChunkEditorError as e:
        await session.rollback()
        raise _handle_editor_error(e) from e

    return ChunkResponse.model_validate(chunk)


@router.post(
    "/{workspace_id}/chunks/{chunk_id}/split",
    response_model=SplitChunkResponse,
)
async def split_chunk(
    workspace_id: UUID,
    chunk_id: UUID,
    body: SplitChunkRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(scoped_workspace),
) -> SplitChunkResponse:
    """Split a chunk at a character position."""
    try:
        first, second, deleted_id = await _editor.split_chunk(
            session,
            workspace_id,
            chunk_id,
            user=portal_actor(request, body.user),
            position=body.position,
        )
        await session.commit()
        await session.refresh(first)
        await session.refresh(second)
    except ChunkEditorError as e:
        await session.rollback()
        raise _handle_editor_error(e) from e

    return SplitChunkResponse(
        first=ChunkResponse.model_validate(first),
        second=ChunkResponse.model_validate(second),
        deleted_chunk_id=deleted_id,
    )


@router.post(
    "/{workspace_id}/chunks/merge",
    response_model=MergeChunksResponse,
)
async def merge_chunks(
    workspace_id: UUID,
    body: MergeChunksRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(scoped_workspace),
) -> MergeChunksResponse:
    """Merge multiple chunks into one."""
    try:
        merged, deleted_ids = await _editor.merge_chunks(
            session,
            workspace_id,
            user=portal_actor(request, body.user),
            chunk_ids=body.chunk_ids,
            separator=body.separator,
        )
        await session.commit()
        await session.refresh(merged)
    except ChunkEditorError as e:
        await session.rollback()
        raise _handle_editor_error(e) from e

    return MergeChunksResponse(
        merged=ChunkResponse.model_validate(merged),
        deleted_chunk_ids=deleted_ids,
    )


@router.post(
    "/{workspace_id}/chunks/reorder",
    response_model=ChunkListResponse,
)
async def reorder_chunks(
    workspace_id: UUID,
    body: ReorderChunksRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(scoped_workspace),
) -> ChunkListResponse:
    """Reorder chunks by providing an ordered list of chunk IDs."""
    try:
        chunks = await _editor.reorder_chunks(
            session,
            workspace_id,
            user=portal_actor(request, body.user),
            chunk_ids=body.chunk_ids,
        )
        await session.commit()
        for c in chunks:
            await session.refresh(c)
    except ChunkEditorError as e:
        await session.rollback()
        raise _handle_editor_error(e) from e

    return ChunkListResponse(
        items=[ChunkResponse.model_validate(c) for c in chunks],
        total=len(chunks),
        offset=0,
        limit=len(chunks),
    )


@router.post(
    "/{workspace_id}/chunks",
    response_model=ChunkResponse,
    status_code=201,
)
async def add_chunk(
    workspace_id: UUID,
    body: AddChunkRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(scoped_workspace),
) -> ChunkResponse:
    """Add a new chunk to a workspace."""
    try:
        chunk = await _editor.add_chunk(
            session,
            workspace_id,
            user=portal_actor(request, body.user),
            text=body.text,
            metadata=body.metadata,
            chunk_index=body.chunk_index,
            staged_document_id=body.staged_document_id,
        )
        await session.commit()
        await session.refresh(chunk)
    except ChunkEditorError as e:
        await session.rollback()
        raise _handle_editor_error(e) from e

    return ChunkResponse.model_validate(chunk)


@router.delete(
    "/{workspace_id}/chunks/{chunk_id}",
    status_code=204,
)
async def delete_chunk(
    workspace_id: UUID,
    chunk_id: UUID,
    request: Request,
    user: str | None = Query(None, min_length=1, max_length=255),
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(scoped_workspace),
) -> None:
    """Delete a chunk from a workspace."""
    try:
        await _editor.delete_chunk(
            session, workspace_id, chunk_id, user=portal_actor(request, user)
        )
        await session.commit()
    except ChunkEditorError as e:
        await session.rollback()
        raise _handle_editor_error(e) from e


@router.post(
    "/{workspace_id}/chunks/{chunk_id}/approve",
    response_model=ChunkResponse,
)
async def approve_chunk(
    workspace_id: UUID,
    chunk_id: UUID,
    request: Request,
    body: ApproveRejectRequest | None = None,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(scoped_workspace),
) -> ChunkResponse:
    """Approve a chunk."""
    body = body or ApproveRejectRequest()
    try:
        chunk = await _editor.approve_chunk(
            session,
            workspace_id,
            chunk_id,
            user=portal_actor(request, body.user),
            reason=body.reason,
        )
        await session.commit()
        await session.refresh(chunk)
    except ChunkEditorError as e:
        await session.rollback()
        raise _handle_editor_error(e) from e

    return ChunkResponse.model_validate(chunk)


@router.post(
    "/{workspace_id}/chunks/{chunk_id}/reject",
    response_model=ChunkResponse,
)
async def reject_chunk(
    workspace_id: UUID,
    chunk_id: UUID,
    request: Request,
    body: ApproveRejectRequest | None = None,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(scoped_workspace),
) -> ChunkResponse:
    """Reject a chunk."""
    body = body or ApproveRejectRequest()
    try:
        chunk = await _editor.reject_chunk(
            session,
            workspace_id,
            chunk_id,
            user=portal_actor(request, body.user),
            reason=body.reason,
        )
        await session.commit()
        await session.refresh(chunk)
    except ChunkEditorError as e:
        await session.rollback()
        raise _handle_editor_error(e) from e

    return ChunkResponse.model_validate(chunk)
