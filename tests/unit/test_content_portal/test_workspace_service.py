"""Unit tests for WorkspaceService — CRUD and lock management."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.content_portal.models.workspace import (
    WorkflowMode,
    Workspace,
    WorkspaceStatus,
)
from callobuzz_rag.content_portal.services.workspace_service import WorkspaceService
from callobuzz_rag.core.exceptions import WorkspaceLockedError, WorkspaceStateError


@pytest.fixture
def service() -> WorkspaceService:
    return WorkspaceService()


def _make_workspace(
    status: str = WorkspaceStatus.DRAFT,
    locked_by: str | None = None,
    locked_at: datetime | None = None,
    corpus_id: uuid.UUID | None = None,
) -> Workspace:
    """Create a mock workspace for testing."""
    ws = MagicMock(spec=Workspace)
    ws.id = uuid.uuid4()
    ws.corpus_id = corpus_id or uuid.uuid4()
    ws.title = "Test Workspace"
    ws.status = status
    ws.workflow_mode = WorkflowMode.SINGLE_REVIEWER
    ws.created_by = "creator"
    ws.assigned_reviewer = None
    ws.locked_by = locked_by
    ws.locked_at = locked_at
    ws.review_feedback = None
    ws.created_at = datetime.now(UTC)
    ws.updated_at = datetime.now(UTC)
    return ws


class TestCreateWorkspace:
    @pytest.mark.asyncio
    async def test_create_sets_draft_status(self, service: WorkspaceService) -> None:
        """New workspace is created with draft status."""
        session = AsyncMock()
        corpus_id = uuid.uuid4()

        workspace = await service.create_workspace(
            session=session,
            corpus_id=corpus_id,
            title="New WS",
            created_by="user1",
        )

        assert workspace.status == WorkspaceStatus.DRAFT
        assert workspace.corpus_id == corpus_id
        assert workspace.title == "New WS"
        assert workspace.created_by == "user1"
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_with_workflow_mode(self, service: WorkspaceService) -> None:
        """Workspace can be created with maker_checker mode."""
        session = AsyncMock()

        workspace = await service.create_workspace(
            session=session,
            corpus_id=uuid.uuid4(),
            title="MC Workspace",
            created_by="user1",
            workflow_mode=WorkflowMode.MAKER_CHECKER,
            assigned_reviewer="reviewer@example.com",
        )

        assert workspace.workflow_mode == WorkflowMode.MAKER_CHECKER
        assert workspace.assigned_reviewer == "reviewer@example.com"


class TestUpdateWorkspace:
    @pytest.mark.asyncio
    async def test_update_title(self, service: WorkspaceService) -> None:
        """Title can be updated on draft workspace."""
        session = AsyncMock()
        workspace = _make_workspace()

        await service.update_workspace(
            session=session,
            workspace=workspace,
            title="Updated Title",
        )

        assert workspace.title == "Updated Title"
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_ingested_raises(self, service: WorkspaceService) -> None:
        """Cannot update workspace in ingested state."""
        session = AsyncMock()
        workspace = _make_workspace(status=WorkspaceStatus.INGESTED)

        with pytest.raises(WorkspaceStateError, match="ingested"):
            await service.update_workspace(
                session=session,
                workspace=workspace,
                title="New Title",
            )

    @pytest.mark.asyncio
    async def test_update_archived_raises(self, service: WorkspaceService) -> None:
        """Cannot update workspace in archived state."""
        session = AsyncMock()
        workspace = _make_workspace(status=WorkspaceStatus.ARCHIVED)

        with pytest.raises(WorkspaceStateError, match="archived"):
            await service.update_workspace(
                session=session,
                workspace=workspace,
                title="New Title",
            )


class TestDeleteWorkspace:
    @pytest.mark.asyncio
    async def test_delete_draft(self, service: WorkspaceService) -> None:
        """Draft workspace can be deleted."""
        session = AsyncMock()
        workspace = _make_workspace(status=WorkspaceStatus.DRAFT)

        await service.delete_workspace(session=session, workspace=workspace)

        session.delete.assert_awaited_once_with(workspace)
        session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_non_draft_raises(self, service: WorkspaceService) -> None:
        """Non-draft workspace cannot be deleted."""
        session = AsyncMock()
        workspace = _make_workspace(status=WorkspaceStatus.IN_REVIEW)

        with pytest.raises(WorkspaceStateError, match="Can only delete draft"):
            await service.delete_workspace(session=session, workspace=workspace)


class TestAcquireLock:
    @pytest.mark.asyncio
    async def test_lock_unlocked_workspace(self, service: WorkspaceService) -> None:
        """Can acquire lock on unlocked workspace."""
        session = AsyncMock()
        workspace = _make_workspace()

        result = await service.acquire_lock(
            session=session,
            workspace=workspace,
            user="editor1",
        )

        assert result is workspace, "acquire_lock must return the locked workspace"
        assert workspace.locked_by == "editor1"
        assert workspace.locked_at is not None
        session.flush.assert_awaited()

    @pytest.mark.asyncio
    async def test_lock_refresh_same_user(self, service: WorkspaceService) -> None:
        """Same user re-locking refreshes the lock timestamp."""
        session = AsyncMock()
        old_time = datetime.now(UTC) - timedelta(minutes=10)
        workspace = _make_workspace(locked_by="editor1", locked_at=old_time)

        await service.acquire_lock(
            session=session,
            workspace=workspace,
            user="editor1",
        )

        assert workspace.locked_by == "editor1"
        assert workspace.locked_at > old_time

    @pytest.mark.asyncio
    async def test_lock_by_other_user_raises(self, service: WorkspaceService) -> None:
        """Cannot lock workspace held by another user (fresh lock)."""
        session = AsyncMock()
        recent = datetime.now(UTC) - timedelta(minutes=5)
        workspace = _make_workspace(locked_by="editor1", locked_at=recent)

        with pytest.raises(WorkspaceLockedError, match="locked by 'editor1'"):
            await service.acquire_lock(
                session=session,
                workspace=workspace,
                user="editor2",
                lock_timeout_minutes=30,
            )

    @pytest.mark.asyncio
    async def test_stale_lock_auto_released(self, service: WorkspaceService) -> None:
        """Stale lock is auto-released and granted to new user."""
        session = AsyncMock()
        old_time = datetime.now(UTC) - timedelta(minutes=60)
        workspace = _make_workspace(locked_by="editor1", locked_at=old_time)

        await service.acquire_lock(
            session=session,
            workspace=workspace,
            user="editor2",
            lock_timeout_minutes=30,
        )

        assert workspace.locked_by == "editor2"

    @pytest.mark.asyncio
    async def test_lock_ingested_raises(self, service: WorkspaceService) -> None:
        """Cannot lock workspace in ingested state."""
        session = AsyncMock()
        workspace = _make_workspace(status=WorkspaceStatus.INGESTED)

        with pytest.raises(WorkspaceStateError, match="ingested"):
            await service.acquire_lock(
                session=session,
                workspace=workspace,
                user="editor1",
            )

    @pytest.mark.asyncio
    async def test_zero_timeout_never_auto_releases(self, service: WorkspaceService) -> None:
        """Lock timeout of 0 means locks never auto-release."""
        session = AsyncMock()
        old_time = datetime.now(UTC) - timedelta(hours=24)
        workspace = _make_workspace(locked_by="editor1", locked_at=old_time)

        with pytest.raises(WorkspaceLockedError, match="locked by 'editor1'"):
            await service.acquire_lock(
                session=session,
                workspace=workspace,
                user="editor2",
                lock_timeout_minutes=0,
            )


class TestReleaseLock:
    @pytest.mark.asyncio
    async def test_release_own_lock(self, service: WorkspaceService) -> None:
        """Lock holder can release their lock."""
        session = AsyncMock()
        workspace = _make_workspace(
            locked_by="editor1",
            locked_at=datetime.now(UTC),
        )

        await service.release_lock(
            session=session,
            workspace=workspace,
            user="editor1",
        )

        assert workspace.locked_by is None
        assert workspace.locked_at is None

    @pytest.mark.asyncio
    async def test_release_other_user_raises(self, service: WorkspaceService) -> None:
        """Cannot release lock held by another user."""
        session = AsyncMock()
        workspace = _make_workspace(
            locked_by="editor1",
            locked_at=datetime.now(UTC),
        )

        with pytest.raises(WorkspaceLockedError, match="cannot be released"):
            await service.release_lock(
                session=session,
                workspace=workspace,
                user="editor2",
            )

    @pytest.mark.asyncio
    async def test_release_unlocked_is_noop(self, service: WorkspaceService) -> None:
        """Releasing an unlocked workspace is a no-op."""
        session = AsyncMock()
        workspace = _make_workspace()

        await service.release_lock(
            session=session,
            workspace=workspace,
            user="anyone",
        )

        # No error, no flush (workspace unchanged)


class TestIsLockStale:
    def test_unlocked_is_not_stale(self, service: WorkspaceService) -> None:
        """Unlocked workspace has no stale lock."""
        workspace = _make_workspace()
        assert service.is_lock_stale(workspace, 30) is False

    def test_fresh_lock_is_not_stale(self, service: WorkspaceService) -> None:
        """Recently locked workspace is not stale."""
        workspace = _make_workspace(
            locked_by="editor1",
            locked_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        assert service.is_lock_stale(workspace, 30) is False

    def test_expired_lock_is_stale(self, service: WorkspaceService) -> None:
        """Lock past timeout is stale."""
        workspace = _make_workspace(
            locked_by="editor1",
            locked_at=datetime.now(UTC) - timedelta(minutes=60),
        )
        assert service.is_lock_stale(workspace, 30) is True

    def test_zero_timeout_never_stale(self, service: WorkspaceService) -> None:
        """Lock timeout of 0 means never stale."""
        workspace = _make_workspace(
            locked_by="editor1",
            locked_at=datetime.now(UTC) - timedelta(hours=24),
        )
        assert service.is_lock_stale(workspace, 0) is False
