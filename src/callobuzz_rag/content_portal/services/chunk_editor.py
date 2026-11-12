"""Chunk editor service — CRUD, split, merge, reorder, approve/reject."""

import logging
import uuid as uuid_mod
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.core.exceptions import ContentPortalError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ChunkEditorError(ContentPortalError):
    """Base exception for chunk editor operations."""


class WorkspaceLockError(ChunkEditorError):
    """Workspace is not locked by the requesting user."""


class ChunkNotFoundError(ChunkEditorError):
    """Chunk not found in workspace."""


class InvalidOperationError(ChunkEditorError):
    """The requested operation is invalid."""


class ParentChunkDerivedError(ChunkEditorError):
    """In a post-ingest review, a parent chunk's copy cannot have its text changed.

    Its text is re-derived from its children when the review is finalized, so an edit,
    split or merge of it would be lost (or, for a split, repeat the children's text).
    """

    code = "parent_chunk_derived"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_history_entry(
    user: str,
    action: str,
    *,
    old_text: str | None = None,
    new_text: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a single edit-history entry dict."""
    entry: dict[str, Any] = {
        "user": user,
        "action": action,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if old_text is not None:
        entry["old_text"] = old_text
    if new_text is not None:
        entry["new_text"] = new_text
    if details is not None:
        entry["details"] = details
    return entry


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ChunkEditorService:
    """Stateless service handling all chunk editing operations."""

    # -- internal helpers ---------------------------------------------------

    async def _verify_workspace_lock(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        user: str,
    ) -> None:
        """Verify the workspace is locked by *user*.

        Raises:
            WorkspaceLockError: If workspace is not locked or locked by someone else.
        """
        from callobuzz_rag.content_portal.models.workspace import Workspace

        ws = await session.get(Workspace, workspace_id)
        if ws is None:
            raise ChunkNotFoundError(f"Workspace {workspace_id} not found")
        if ws.locked_by is None:
            raise WorkspaceLockError(
                f"Workspace {workspace_id} is not locked — acquire a lock first"
            )
        if ws.locked_by != user:
            raise WorkspaceLockError(
                f"Workspace {workspace_id} is locked by '{ws.locked_by}', not '{user}'"
            )

    async def _get_chunk(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        chunk_id: UUID,
    ) -> "StagedChunk":  # type: ignore[name-defined]
        """Fetch a chunk, verifying it belongs to the workspace.

        Raises:
            ChunkNotFoundError: If chunk doesn't exist or belongs to another workspace.
        """
        from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk

        chunk = await session.get(StagedChunk, chunk_id)
        if chunk is None or chunk.workspace_id != workspace_id:
            raise ChunkNotFoundError(f"Chunk {chunk_id} not found in workspace {workspace_id}")
        return chunk

    async def _get_next_chunk_index(
        self,
        session: AsyncSession,
        workspace_id: UUID,
    ) -> int:
        """Return max(chunk_index) + 1 for the workspace, or 0 if empty."""
        from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk

        result = await session.execute(
            select(func.max(StagedChunk.chunk_index)).where(
                StagedChunk.workspace_id == workspace_id
            )
        )
        max_idx = result.scalar_one_or_none()
        return 0 if max_idx is None else max_idx + 1

    async def _make_room(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        index: int,
    ) -> None:
        """Move every chunk at `index` or later one place down, so a new one fits there.

        Without it a split's second half (or a chunk added at a position) shared its
        index with the next chunk, and `_reindex_chunks` ordered the tie arbitrarily.
        """
        from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk

        result = await session.execute(
            select(StagedChunk).where(
                StagedChunk.workspace_id == workspace_id,
                StagedChunk.chunk_index >= index,
            )
        )
        for chunk in result.scalars().all():
            chunk.chunk_index += 1

    async def _reindex_chunks(
        self,
        session: AsyncSession,
        workspace_id: UUID,
    ) -> None:
        """Re-assign sequential 0,1,2... indices ordered by current chunk_index."""
        from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk

        result = await session.execute(
            select(StagedChunk)
            .where(StagedChunk.workspace_id == workspace_id)
            .order_by(StagedChunk.chunk_index)
        )
        chunks = list(result.scalars().all())
        for idx, chunk in enumerate(chunks):
            chunk.chunk_index = idx
        await session.flush()

    async def _refuse_parent_copies(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        chunks: list[Any],
        *,
        new_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Refuse a text change to a parent chunk's copy in a post-ingest review.

        `chunks` are the staged chunks whose text the operation changes; `new_metadata`
        (of a chunk being added) must not point at a parent either.
        """
        from callobuzz_rag.content_portal.services.post_ingest_review import (
            PARENT_CHUNK_DERIVED_MESSAGE,
            engine_chunk_id_in,
            review_parent_ids,
        )

        parents = await review_parent_ids(session, workspace_id)
        if not parents:
            return
        if any(engine_chunk_id_in(c.metadata_) in parents for c in chunks):
            raise ParentChunkDerivedError(PARENT_CHUNK_DERIVED_MESSAGE)
        if new_metadata is not None and engine_chunk_id_in(new_metadata) in parents:
            raise ParentChunkDerivedError(PARENT_CHUNK_DERIVED_MESSAGE)

    async def _refuse_relinking_a_parent(
        self, session: AsyncSession, workspace_id: UUID, chunk: Any, metadata: dict[str, Any]
    ) -> None:
        """A metadata edit may not change which chunk a parent copy names, nor point
        another chunk at a parent."""
        from callobuzz_rag.content_portal.services.post_ingest_review import (
            PARENT_CHUNK_DERIVED_MESSAGE,
            engine_chunk_id_in,
            review_parent_ids,
        )

        parents = await review_parent_ids(session, workspace_id)
        if not parents:
            return
        current = engine_chunk_id_in(chunk.metadata_)
        new = engine_chunk_id_in(metadata)
        if new != current and (current in parents or new in parents):
            raise ParentChunkDerivedError(PARENT_CHUNK_DERIVED_MESSAGE)

    # -- public API ---------------------------------------------------------

    async def list_chunks(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        *,
        offset: int = 0,
        limit: int = 50,
        status_filter: str | None = None,
    ) -> tuple[list[Any], int]:
        """Return (chunks, total) for a workspace with pagination."""
        from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk

        base = select(StagedChunk).where(StagedChunk.workspace_id == workspace_id)
        count_q = select(func.count(StagedChunk.id)).where(StagedChunk.workspace_id == workspace_id)

        if status_filter:
            base = base.where(StagedChunk.status == status_filter)
            count_q = count_q.where(StagedChunk.status == status_filter)

        total_result = await session.execute(count_q)
        total = total_result.scalar_one()

        result = await session.execute(
            base.order_by(StagedChunk.chunk_index).offset(offset).limit(limit)
        )
        items = list(result.scalars().all())
        return items, total

    async def edit_chunk(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        chunk_id: UUID,
        *,
        user: str,
        text: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "StagedChunk":  # type: ignore[name-defined]
        """Edit a chunk's text and/or metadata.

        Raises:
            InvalidOperationError: If neither text nor metadata is provided.
            WorkspaceLockError: If workspace not locked by user.
            ChunkNotFoundError: If chunk not found.
        """
        if text is None and metadata is None:
            raise InvalidOperationError("At least one of text or metadata must be provided")

        await self._verify_workspace_lock(session, workspace_id, user)
        chunk = await self._get_chunk(session, workspace_id, chunk_id)
        if text is not None and text != chunk.text:
            await self._refuse_parent_copies(session, workspace_id, [chunk])
        if metadata is not None:
            await self._refuse_relinking_a_parent(session, workspace_id, chunk, metadata)

        history = list(chunk.edit_history or []) if chunk.edit_history else []

        if text is not None and text != chunk.text:
            history.append(
                _make_history_entry(user, "edit_text", old_text=chunk.text, new_text=text)
            )
            chunk.text = text

        if metadata is not None:
            history.append(
                _make_history_entry(user, "edit_metadata", details={"metadata": metadata})
            )
            chunk.metadata_ = metadata

        chunk.edit_history = history
        await session.flush()
        return chunk

    async def split_chunk(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        chunk_id: UUID,
        *,
        user: str,
        position: int,
    ) -> tuple[Any, ...]:
        """Split a chunk at *position* into two new chunks.

        Returns:
            (first_chunk, second_chunk, deleted_chunk_id)

        Raises:
            InvalidOperationError: If position is out of bounds.
        """
        from callobuzz_rag.content_portal.models.staged_chunk import (
            StagedChunk,
            StagedChunkSource,
            StagedChunkStatus,
        )

        await self._verify_workspace_lock(session, workspace_id, user)
        chunk = await self._get_chunk(session, workspace_id, chunk_id)
        await self._refuse_parent_copies(session, workspace_id, [chunk])

        if position < 1 or position >= len(chunk.text):
            raise InvalidOperationError(
                f"Split position {position} out of bounds for text length {len(chunk.text)}"
            )

        first_text = chunk.text[:position].rstrip()
        second_text = chunk.text[position:].lstrip()
        deleted_id = chunk.id
        original_index = chunk.chunk_index

        history_entry = _make_history_entry(
            user, "split", details={"position": position, "source_chunk_id": str(deleted_id)}
        )

        first = StagedChunk(
            id=uuid_mod.uuid4(),
            workspace_id=workspace_id,
            staged_document_id=chunk.staged_document_id,
            text=first_text,
            chunk_index=original_index,
            metadata_=chunk.metadata_,
            status=StagedChunkStatus.DRAFT,
            source=StagedChunkSource.MANUAL,
            edit_history=[history_entry],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        second = StagedChunk(
            id=uuid_mod.uuid4(),
            workspace_id=workspace_id,
            staged_document_id=chunk.staged_document_id,
            text=second_text,
            chunk_index=original_index + 1,
            metadata_=chunk.metadata_,
            status=StagedChunkStatus.DRAFT,
            source=StagedChunkSource.MANUAL,
            edit_history=[history_entry],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        await session.delete(chunk)
        await session.flush()
        await self._make_room(session, workspace_id, original_index + 1)
        session.add(first)
        session.add(second)
        await session.flush()
        await self._reindex_chunks(session, workspace_id)

        return first, second, deleted_id

    async def merge_chunks(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        *,
        user: str,
        chunk_ids: list[UUID],
        separator: str = "\n\n",
    ) -> tuple[Any, ...]:
        """Merge multiple chunks into one.

        Returns:
            (merged_chunk, deleted_chunk_ids)

        Raises:
            InvalidOperationError: If fewer than 2 chunk_ids given.
        """
        from callobuzz_rag.content_portal.models.staged_chunk import (
            StagedChunk,
            StagedChunkSource,
            StagedChunkStatus,
        )

        if len(chunk_ids) < 2:
            raise InvalidOperationError("At least 2 chunks are required for merge")

        await self._verify_workspace_lock(session, workspace_id, user)

        # Fetch all chunks in order
        chunks = []
        for cid in chunk_ids:
            chunks.append(await self._get_chunk(session, workspace_id, cid))
        await self._refuse_parent_copies(session, workspace_id, chunks)

        # Sort by chunk_index to merge in order
        chunks.sort(key=lambda c: c.chunk_index)
        merged_text = separator.join(c.text for c in chunks)
        min_index = min(c.chunk_index for c in chunks)
        deleted_ids = [c.id for c in chunks]

        history_entry = _make_history_entry(
            user,
            "merge",
            details={"source_chunk_ids": [str(cid) for cid in deleted_ids]},
        )

        merged = StagedChunk(
            id=uuid_mod.uuid4(),
            workspace_id=workspace_id,
            staged_document_id=chunks[0].staged_document_id,
            text=merged_text,
            chunk_index=min_index,
            # The first chunk's metadata, as a split keeps its source's: headings and
            # (in a post-ingest review) the chunk it came from, for finalize-review.
            metadata_=dict(chunks[0].metadata_) if chunks[0].metadata_ else None,
            status=StagedChunkStatus.DRAFT,
            source=StagedChunkSource.MANUAL,
            edit_history=[history_entry],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        for c in chunks:
            await session.delete(c)
        session.add(merged)
        await session.flush()
        await self._reindex_chunks(session, workspace_id)

        return merged, deleted_ids

    async def reorder_chunks(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        *,
        user: str,
        chunk_ids: list[UUID],
    ) -> list[Any]:
        """Reorder chunks according to the supplied ID order.

        Returns:
            list of reordered chunks.
        """
        await self._verify_workspace_lock(session, workspace_id, user)

        chunks = []
        for cid in chunk_ids:
            chunks.append(await self._get_chunk(session, workspace_id, cid))

        history_entry = _make_history_entry(user, "reorder")

        for idx, chunk in enumerate(chunks):
            chunk.chunk_index = idx
            history = list(chunk.edit_history or []) if chunk.edit_history else []
            history.append(history_entry)
            chunk.edit_history = history

        await session.flush()
        return chunks

    async def add_chunk(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        *,
        user: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        chunk_index: int | None = None,
        staged_document_id: UUID | None = None,
    ) -> "StagedChunk":  # type: ignore[name-defined]
        """Add a new chunk to the workspace.

        Raises:
            ChunkNotFoundError: `staged_document_id` names a staged document that is
                not in this workspace (another workspace's, possibly another
                tenant's, which ingest would then read metadata from).
        """
        from callobuzz_rag.content_portal.models.staged_chunk import (
            StagedChunk,
            StagedChunkSource,
            StagedChunkStatus,
        )
        from callobuzz_rag.content_portal.models.staged_document import StagedDocument

        if staged_document_id is not None:
            staged_document = await session.get(StagedDocument, staged_document_id)
            if staged_document is None or staged_document.workspace_id != workspace_id:
                raise ChunkNotFoundError("Staged document not found in this workspace")

        await self._verify_workspace_lock(session, workspace_id, user)
        if metadata is not None:
            await self._refuse_parent_copies(session, workspace_id, [], new_metadata=metadata)

        if chunk_index is None:
            chunk_index = await self._get_next_chunk_index(session, workspace_id)
        else:
            await self._make_room(session, workspace_id, chunk_index)

        history_entry = _make_history_entry(user, "create")

        chunk = StagedChunk(
            id=uuid_mod.uuid4(),
            workspace_id=workspace_id,
            staged_document_id=staged_document_id,
            text=text,
            chunk_index=chunk_index,
            metadata_=metadata,
            status=StagedChunkStatus.DRAFT,
            source=StagedChunkSource.MANUAL,
            edit_history=[history_entry],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(chunk)
        await session.flush()
        await self._reindex_chunks(session, workspace_id)
        return chunk

    async def delete_chunk(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        chunk_id: UUID,
        *,
        user: str,
    ) -> None:
        """Delete a chunk and reindex remaining chunks."""
        await self._verify_workspace_lock(session, workspace_id, user)
        chunk = await self._get_chunk(session, workspace_id, chunk_id)
        await session.delete(chunk)
        await session.flush()
        await self._reindex_chunks(session, workspace_id)

    async def approve_chunk(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        chunk_id: UUID,
        *,
        user: str,
        reason: str | None = None,
    ) -> "StagedChunk":  # type: ignore[name-defined]
        """Set chunk status to approved."""
        from callobuzz_rag.content_portal.models.staged_chunk import StagedChunkStatus

        await self._verify_workspace_lock(session, workspace_id, user)
        chunk = await self._get_chunk(session, workspace_id, chunk_id)

        history = list(chunk.edit_history or []) if chunk.edit_history else []
        details = {"reason": reason} if reason else None
        history.append(_make_history_entry(user, "approve", details=details))

        chunk.status = StagedChunkStatus.APPROVED
        chunk.edit_history = history
        await session.flush()
        return chunk

    async def reject_chunk(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        chunk_id: UUID,
        *,
        user: str,
        reason: str | None = None,
    ) -> "StagedChunk":  # type: ignore[name-defined]
        """Set chunk status to rejected."""
        from callobuzz_rag.content_portal.models.staged_chunk import StagedChunkStatus

        await self._verify_workspace_lock(session, workspace_id, user)
        chunk = await self._get_chunk(session, workspace_id, chunk_id)

        history = list(chunk.edit_history or []) if chunk.edit_history else []
        details = {"reason": reason} if reason else None
        history.append(_make_history_entry(user, "reject", details=details))

        chunk.status = StagedChunkStatus.REJECTED
        chunk.edit_history = history
        await session.flush()
        return chunk
