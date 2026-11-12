"""Workspace CRUD and lock management service."""

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.content_portal.models.workspace import (
    WorkflowMode,
    Workspace,
    WorkspaceKind,
    WorkspaceStatus,
)
from callobuzz_rag.core.exceptions import WorkspaceLockedError, WorkspaceStateError

logger = logging.getLogger(__name__)


class WorkspaceService:
    """Workspace CRUD operations and lock management.

    All methods take an AsyncSession parameter for transaction control.
    The caller (route handler) is responsible for commit/rollback.
    """

    async def create_workspace(
        self,
        session: AsyncSession,
        corpus_id: uuid.UUID,
        title: str,
        created_by: str,
        workflow_mode: str = WorkflowMode.SINGLE_REVIEWER,
        assigned_reviewer: str | None = None,
    ) -> Workspace:
        """Create a new workspace in draft status.

        Args:
            session: Database session.
            corpus_id: ID of the corpus this workspace belongs to.
            title: Workspace title.
            created_by: User identifier who created the workspace.
            workflow_mode: Review workflow mode.
            assigned_reviewer: Reviewer for maker-checker mode.

        Returns:
            The newly created Workspace.
        """
        workspace = Workspace(
            corpus_id=corpus_id,
            title=title,
            kind=WorkspaceKind.STANDARD,
            status=WorkspaceStatus.DRAFT,
            workflow_mode=workflow_mode,
            created_by=created_by,
            assigned_reviewer=assigned_reviewer,
        )
        session.add(workspace)
        await session.flush()
        logger.info(
            "Created workspace %s for corpus %s by %s",
            workspace.id,
            corpus_id,
            created_by,
        )
        return workspace

    async def get_workspace(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        corpus_id: uuid.UUID,
        *,
        include_stats: bool = False,
    ) -> Workspace | None:
        """Get a workspace by ID, scoped to a corpus.

        Args:
            session: Database session.
            workspace_id: Workspace UUID.
            corpus_id: Corpus UUID for scoping.
            include_stats: If True, populate content_count, chunk_count,
                and approved_chunk_count on the returned workspace.

        Returns:
            Workspace if found and belongs to corpus, None otherwise.
        """
        stmt = select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.corpus_id == corpus_id,
        )
        result = await session.execute(stmt)
        workspace = result.scalar_one_or_none()

        if workspace is not None and include_stats:
            await self.load_stats(session, workspace)

        return workspace

    async def load_stats(self, session: AsyncSession, workspace: Workspace) -> Workspace:
        """Populate content_count, chunk_count and approved_chunk_count on a workspace."""
        await self.load_stats_many(session, [workspace])
        return workspace

    async def load_stats_many(self, session: AsyncSession, workspaces: Sequence[Workspace]) -> None:
        """Populate content_count, chunk_count and approved_chunk_count on each workspace.

        One statement for the whole page: staged content and staged chunks are each
        counted grouped by workspace, and outer-joined to the page's workspace ids.
        """
        from callobuzz_rag.content_portal.models.staged_chunk import (
            StagedChunk,
            StagedChunkStatus,
        )
        from callobuzz_rag.content_portal.models.staged_content import StagedContent

        if not workspaces:
            return
        ids = [w.id for w in workspaces]
        contents = (
            select(
                StagedContent.workspace_id.label("workspace_id"),
                func.count().label("n"),
            )
            .where(StagedContent.workspace_id.in_(ids))
            .group_by(StagedContent.workspace_id)
            .subquery()
        )
        chunks = (
            select(
                StagedChunk.workspace_id.label("workspace_id"),
                func.count().label("n"),
                func.count()
                .filter(StagedChunk.status == StagedChunkStatus.APPROVED)
                .label("approved"),
            )
            .where(StagedChunk.workspace_id.in_(ids))
            .group_by(StagedChunk.workspace_id)
            .subquery()
        )
        stmt = (
            select(
                Workspace.id,
                func.coalesce(contents.c.n, 0),
                func.coalesce(chunks.c.n, 0),
                func.coalesce(chunks.c.approved, 0),
            )
            .outerjoin(contents, contents.c.workspace_id == Workspace.id)
            .outerjoin(chunks, chunks.c.workspace_id == Workspace.id)
            .where(Workspace.id.in_(ids))
        )
        stats = {
            row[0]: (int(row[1]), int(row[2]), int(row[3]))
            for row in (await session.execute(stmt)).all()
        }
        for workspace in workspaces:
            content_count, chunk_count, approved = stats.get(workspace.id, (0, 0, 0))
            workspace.content_count = content_count  # type: ignore[attr-defined]
            workspace.chunk_count = chunk_count  # type: ignore[attr-defined]
            workspace.approved_chunk_count = approved  # type: ignore[attr-defined]

    async def list_workspaces(
        self,
        session: AsyncSession,
        *,
        corpus_ids: Sequence[uuid.UUID] | None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Workspace], int]:
        """List workspaces of some corpora with optional status filter.

        Args:
            session: Database session.
            corpus_ids: The corpora to list, as `authorized_corpus_ids` returns
                them: None means every corpus (admin only); an empty sequence
                matches nothing. Required, so no caller lists everything by omission.
            status: Optional status filter.
            offset: Pagination offset.
            limit: Pagination limit (max 100).

        Returns:
            Tuple of (workspace list, total count).
        """
        limit = min(limit, 100)

        conditions: list[ColumnElement[bool]] = []
        if corpus_ids is not None:
            conditions.append(Workspace.corpus_id.in_(list(corpus_ids)))
        if status:
            conditions.append(Workspace.status == status)

        # Count
        count_stmt = select(func.count()).select_from(Workspace).where(*conditions)
        count_result = await session.execute(count_stmt)
        total = count_result.scalar_one()

        # Items
        stmt = (
            select(Workspace)
            .where(*conditions)
            .order_by(Workspace.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await session.execute(stmt)
        items = list(result.scalars().all())

        return items, total

    async def update_workspace(
        self,
        session: AsyncSession,
        workspace: Workspace,
        title: str | None = None,
        assigned_reviewer: str | None = None,
    ) -> Workspace:
        """Update workspace fields.

        Only draft or in_review workspaces can be updated.

        Args:
            session: Database session.
            workspace: Workspace to update.
            title: New title (if provided).
            assigned_reviewer: New reviewer (if provided).

        Returns:
            Updated workspace.

        Raises:
            WorkspaceStateError: If workspace is in a terminal state.
        """
        immutable_states = {
            WorkspaceStatus.INGESTED,
            WorkspaceStatus.ARCHIVED,
        }
        if workspace.status in immutable_states:
            raise WorkspaceStateError(f"Cannot update workspace in '{workspace.status}' state")

        if title is not None:
            workspace.title = title
        if assigned_reviewer is not None:
            workspace.assigned_reviewer = assigned_reviewer

        workspace.updated_at = datetime.now(UTC)
        await session.flush()
        return workspace

    async def delete_workspace(
        self,
        session: AsyncSession,
        workspace: Workspace,
    ) -> None:
        """Delete a workspace (only if draft).

        Args:
            session: Database session.
            workspace: Workspace to delete.

        Raises:
            WorkspaceStateError: If workspace is not in draft state.
        """
        if workspace.status != WorkspaceStatus.DRAFT:
            raise WorkspaceStateError(
                f"Can only delete draft workspaces, current: '{workspace.status}'"
            )

        await session.delete(workspace)
        await session.flush()
        logger.info("Deleted workspace %s", workspace.id)

    async def acquire_lock(
        self,
        session: AsyncSession,
        workspace: Workspace,
        user: str,
        lock_timeout_minutes: int = 30,
    ) -> Workspace:
        """Acquire an edit lock on a workspace.

        If the workspace is already locked by the same user, the lock
        is refreshed. If locked by a different user, checks for stale
        lock timeout before raising.

        Args:
            session: Database session.
            workspace: Workspace to lock.
            user: User identifier requesting the lock.
            lock_timeout_minutes: Minutes before a lock is considered stale.

        Returns:
            Locked workspace.

        Raises:
            WorkspaceLockedError: If locked by another user and lock is fresh.
            WorkspaceStateError: If workspace is in a non-editable state.
        """
        non_editable = {
            WorkspaceStatus.INGESTED,
            WorkspaceStatus.ARCHIVED,
        }
        if workspace.status in non_editable:
            raise WorkspaceStateError(f"Cannot lock workspace in '{workspace.status}' state")

        now = datetime.now(UTC)

        # Already locked by same user — refresh
        if workspace.locked_by == user:
            workspace.locked_at = now
            await session.flush()
            return workspace

        # Locked by another user — check timeout
        if workspace.locked_by is not None and workspace.locked_at is not None:
            if lock_timeout_minutes > 0:
                expiry = workspace.locked_at + timedelta(minutes=lock_timeout_minutes)
                if now < expiry:
                    raise WorkspaceLockedError(
                        f"Workspace locked by '{workspace.locked_by}' until {expiry.isoformat()}"
                    )
                # Stale lock — auto-release and grant to new user
                logger.info(
                    "Auto-releasing stale lock on workspace %s (was held by '%s')",
                    workspace.id,
                    workspace.locked_by,
                )
            else:
                # lock_timeout_minutes <= 0 means never auto-release
                raise WorkspaceLockedError(f"Workspace locked by '{workspace.locked_by}'")

        workspace.locked_by = user
        workspace.locked_at = now
        workspace.updated_at = now
        await session.flush()
        logger.info(
            "Lock acquired on workspace %s by '%s'",
            workspace.id,
            user,
        )
        return workspace

    async def release_lock(
        self,
        session: AsyncSession,
        workspace: Workspace,
        user: str,
    ) -> Workspace:
        """Release an edit lock on a workspace.

        Only the lock holder can release (or admin via force).

        Args:
            session: Database session.
            workspace: Workspace to unlock.
            user: User identifier releasing the lock.

        Returns:
            Unlocked workspace.

        Raises:
            WorkspaceLockedError: If locked by a different user.
        """
        if workspace.locked_by is None:
            # Already unlocked — no-op
            return workspace

        if workspace.locked_by != user:
            raise WorkspaceLockedError(
                f"Lock held by '{workspace.locked_by}', cannot be released by '{user}'"
            )

        workspace.locked_by = None
        workspace.locked_at = None
        workspace.updated_at = datetime.now(UTC)
        await session.flush()
        logger.info(
            "Lock released on workspace %s by '%s'",
            workspace.id,
            user,
        )
        return workspace

    def is_lock_stale(
        self,
        workspace: Workspace,
        lock_timeout_minutes: int = 30,
    ) -> bool:
        """Check if a workspace lock is stale (past timeout).

        Args:
            workspace: Workspace to check.
            lock_timeout_minutes: Timeout in minutes.

        Returns:
            True if locked and lock has expired, False otherwise.
        """
        if workspace.locked_by is None or workspace.locked_at is None:
            return False

        if lock_timeout_minutes <= 0:
            return False

        expiry = workspace.locked_at + timedelta(minutes=lock_timeout_minutes)
        return datetime.now(UTC) > expiry
