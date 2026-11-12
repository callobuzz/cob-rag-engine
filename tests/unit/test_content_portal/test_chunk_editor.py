"""Unit tests for ChunkEditorService — edit, split, merge, reorder, approve/reject."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.content_portal.models.staged_chunk import (
    StagedChunk,
    StagedChunkSource,
    StagedChunkStatus,
)
from callobuzz_rag.content_portal.models.workspace import Workspace
from callobuzz_rag.content_portal.services.chunk_editor import (
    ChunkEditorService,
    ChunkNotFoundError,
    InvalidOperationError,
    WorkspaceLockError,
    _make_history_entry,
)


def _make_workspace(
    locked_by: str | None = None,
) -> MagicMock:
    ws = MagicMock(spec=Workspace)
    ws.id = uuid.uuid4()
    ws.locked_by = locked_by
    return ws


def _make_chunk(
    workspace_id: uuid.UUID,
    text: str = "Hello world",
    chunk_index: int = 0,
    status: str = StagedChunkStatus.DRAFT,
    edit_history: list | None = None,
    staged_document_id: uuid.UUID | None = None,
) -> MagicMock:
    chunk = MagicMock(spec=StagedChunk)
    chunk.id = uuid.uuid4()
    chunk.workspace_id = workspace_id
    chunk.staged_document_id = staged_document_id
    chunk.text = text
    chunk.chunk_index = chunk_index
    chunk.metadata_ = None
    chunk.confidence_score = None
    chunk.warnings = None
    chunk.status = status
    chunk.source = StagedChunkSource.MANUAL
    chunk.edit_history = edit_history
    chunk.created_at = datetime.now(UTC)
    chunk.updated_at = datetime.now(UTC)
    return chunk


def _make_session(
    workspace: MagicMock | None = None,
    chunks: list[MagicMock] | None = None,
) -> AsyncMock:
    """Build an AsyncMock session that returns workspace/chunks from get()."""
    session = AsyncMock()
    chunks = chunks or []
    chunk_map = {c.id: c for c in chunks}

    async def fake_get(model_cls, obj_id):
        if model_cls is Workspace or (
            hasattr(model_cls, "__tablename__") and model_cls.__tablename__ == "portal_workspaces"
        ):
            return workspace
        # StagedChunk
        return chunk_map.get(obj_id)

    session.get = AsyncMock(side_effect=fake_get)
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.flush = AsyncMock()

    # For execute() calls (list_chunks, _get_next_chunk_index, _reindex_chunks)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = chunks
    mock_result.scalar_one_or_none.return_value = (
        max((c.chunk_index for c in chunks), default=None) if chunks else None
    )
    mock_result.scalar_one.return_value = len(chunks)
    session.execute = AsyncMock(return_value=mock_result)

    return session


@pytest.fixture
def service() -> ChunkEditorService:
    return ChunkEditorService()


# ---------------------------------------------------------------------------
# TestMakeHistoryEntry
# ---------------------------------------------------------------------------


class TestMakeHistoryEntry:
    def test_basic_entry(self) -> None:
        """Creates entry with user, action, timestamp."""
        entry = _make_history_entry("alice", "edit_text")
        assert entry["user"] == "alice"
        assert entry["action"] == "edit_text"
        assert "timestamp" in entry

    def test_with_text_fields(self) -> None:
        """Includes old_text and new_text when provided."""
        entry = _make_history_entry("bob", "edit_text", old_text="old", new_text="new")
        assert entry["old_text"] == "old"
        assert entry["new_text"] == "new"

    def test_with_details(self) -> None:
        """Includes details dict when provided."""
        entry = _make_history_entry("charlie", "merge", details={"ids": [1, 2]})
        assert entry["details"] == {"ids": [1, 2]}

    def test_omits_none_fields(self) -> None:
        """Does not include old_text/new_text/details when not provided."""
        entry = _make_history_entry("dave", "reorder")
        assert "old_text" not in entry
        assert "new_text" not in entry
        assert "details" not in entry


# ---------------------------------------------------------------------------
# TestVerifyWorkspaceLock
# ---------------------------------------------------------------------------


class TestVerifyWorkspaceLock:
    @pytest.mark.asyncio
    async def test_locked_by_user_passes(self, service: ChunkEditorService) -> None:
        """No error when workspace is locked by the requesting user."""
        ws = _make_workspace(locked_by="editor1")
        session = _make_session(workspace=ws)
        # Should not raise
        await service._verify_workspace_lock(session, ws.id, "editor1")

    @pytest.mark.asyncio
    async def test_not_locked_raises(self, service: ChunkEditorService) -> None:
        """Raises WorkspaceLockError when workspace is not locked."""
        ws = _make_workspace(locked_by=None)
        session = _make_session(workspace=ws)

        with pytest.raises(WorkspaceLockError, match="not locked"):
            await service._verify_workspace_lock(session, ws.id, "editor1")

    @pytest.mark.asyncio
    async def test_locked_by_other_raises(self, service: ChunkEditorService) -> None:
        """Raises WorkspaceLockError when locked by another user."""
        ws = _make_workspace(locked_by="editor1")
        session = _make_session(workspace=ws)

        with pytest.raises(WorkspaceLockError, match="locked by 'editor1'"):
            await service._verify_workspace_lock(session, ws.id, "editor2")

    @pytest.mark.asyncio
    async def test_workspace_not_found_raises(self, service: ChunkEditorService) -> None:
        """Raises ChunkNotFoundError when workspace doesn't exist."""
        session = _make_session(workspace=None)

        with pytest.raises(ChunkNotFoundError, match="not found"):
            await service._verify_workspace_lock(session, uuid.uuid4(), "editor1")


# ---------------------------------------------------------------------------
# TestEditChunk
# ---------------------------------------------------------------------------


class TestEditChunk:
    @pytest.mark.asyncio
    async def test_edit_text(self, service: ChunkEditorService) -> None:
        """Text is updated and history recorded."""
        ws = _make_workspace(locked_by="editor1")
        chunk = _make_chunk(ws.id, text="original")
        session = _make_session(workspace=ws, chunks=[chunk])

        result = await service.edit_chunk(session, ws.id, chunk.id, user="editor1", text="updated")

        assert result.text == "updated"
        assert len(result.edit_history) == 1
        assert result.edit_history[0]["action"] == "edit_text"
        session.flush.assert_awaited()

    @pytest.mark.asyncio
    async def test_edit_metadata(self, service: ChunkEditorService) -> None:
        """Metadata is updated and history recorded."""
        ws = _make_workspace(locked_by="editor1")
        chunk = _make_chunk(ws.id)
        session = _make_session(workspace=ws, chunks=[chunk])

        result = await service.edit_chunk(
            session, ws.id, chunk.id, user="editor1", metadata={"key": "val"}
        )

        assert result.metadata_ == {"key": "val"}
        assert len(result.edit_history) == 1
        assert result.edit_history[0]["action"] == "edit_metadata"

    @pytest.mark.asyncio
    async def test_edit_unchanged_text_skips_history(self, service: ChunkEditorService) -> None:
        """No text history entry when text is the same."""
        ws = _make_workspace(locked_by="editor1")
        chunk = _make_chunk(ws.id, text="same")
        session = _make_session(workspace=ws, chunks=[chunk])

        result = await service.edit_chunk(session, ws.id, chunk.id, user="editor1", text="same")

        # No history entry for unchanged text
        assert len(result.edit_history) == 0

    @pytest.mark.asyncio
    async def test_edit_both_none_raises(self, service: ChunkEditorService) -> None:
        """Raises InvalidOperationError when both text and metadata are None."""
        ws = _make_workspace(locked_by="editor1")
        session = _make_session(workspace=ws)

        with pytest.raises(InvalidOperationError, match="At least one"):
            await service.edit_chunk(session, ws.id, uuid.uuid4(), user="editor1")

    @pytest.mark.asyncio
    async def test_edit_requires_lock(self, service: ChunkEditorService) -> None:
        """Raises WorkspaceLockError when workspace is not locked by user."""
        ws = _make_workspace(locked_by=None)
        session = _make_session(workspace=ws)

        with pytest.raises(WorkspaceLockError):
            await service.edit_chunk(session, ws.id, uuid.uuid4(), user="editor1", text="new")


# ---------------------------------------------------------------------------
# TestSplitChunk
# ---------------------------------------------------------------------------


class TestSplitChunk:
    @pytest.mark.asyncio
    async def test_split_creates_two_chunks(self, service: ChunkEditorService) -> None:
        """Splitting produces two new chunks and deletes the original."""
        ws = _make_workspace(locked_by="editor1")
        chunk = _make_chunk(ws.id, text="Hello World", chunk_index=0)
        session = _make_session(workspace=ws, chunks=[chunk])

        first, second, deleted_id = await service.split_chunk(
            session, ws.id, chunk.id, user="editor1", position=5
        )

        assert first.text == "Hello"
        assert second.text == "World"
        assert deleted_id == chunk.id
        session.delete.assert_awaited()

    @pytest.mark.asyncio
    async def test_split_position_zero_raises(self, service: ChunkEditorService) -> None:
        """Position 0 is invalid for split."""
        ws = _make_workspace(locked_by="editor1")
        chunk = _make_chunk(ws.id, text="Hello")
        session = _make_session(workspace=ws, chunks=[chunk])

        with pytest.raises(InvalidOperationError, match="out of bounds"):
            await service.split_chunk(session, ws.id, chunk.id, user="editor1", position=0)

    @pytest.mark.asyncio
    async def test_split_position_at_end_raises(self, service: ChunkEditorService) -> None:
        """Position equal to text length is invalid."""
        ws = _make_workspace(locked_by="editor1")
        chunk = _make_chunk(ws.id, text="Hello")
        session = _make_session(workspace=ws, chunks=[chunk])

        with pytest.raises(InvalidOperationError, match="out of bounds"):
            await service.split_chunk(session, ws.id, chunk.id, user="editor1", position=5)

    @pytest.mark.asyncio
    async def test_split_requires_lock(self, service: ChunkEditorService) -> None:
        """Raises WorkspaceLockError when not locked."""
        ws = _make_workspace(locked_by=None)
        session = _make_session(workspace=ws)

        with pytest.raises(WorkspaceLockError):
            await service.split_chunk(session, ws.id, uuid.uuid4(), user="editor1", position=3)


# ---------------------------------------------------------------------------
# TestMergeChunks
# ---------------------------------------------------------------------------


class TestMergeChunks:
    @pytest.mark.asyncio
    async def test_merge_two_chunks(self, service: ChunkEditorService) -> None:
        """Two chunks merge into one with separator."""
        ws = _make_workspace(locked_by="editor1")
        c1 = _make_chunk(ws.id, text="Part A", chunk_index=0)
        c2 = _make_chunk(ws.id, text="Part B", chunk_index=1)
        session = _make_session(workspace=ws, chunks=[c1, c2])

        merged, deleted_ids = await service.merge_chunks(
            session, ws.id, user="editor1", chunk_ids=[c1.id, c2.id]
        )

        assert merged.text == "Part A\n\nPart B"
        assert set(deleted_ids) == {c1.id, c2.id}

    @pytest.mark.asyncio
    async def test_merge_custom_separator(self, service: ChunkEditorService) -> None:
        """Custom separator is used."""
        ws = _make_workspace(locked_by="editor1")
        c1 = _make_chunk(ws.id, text="A", chunk_index=0)
        c2 = _make_chunk(ws.id, text="B", chunk_index=1)
        session = _make_session(workspace=ws, chunks=[c1, c2])

        merged, _ = await service.merge_chunks(
            session, ws.id, user="editor1", chunk_ids=[c1.id, c2.id], separator=" | "
        )

        assert merged.text == "A | B"

    @pytest.mark.asyncio
    async def test_merge_one_chunk_raises(self, service: ChunkEditorService) -> None:
        """Fewer than 2 chunks raises InvalidOperationError."""
        ws = _make_workspace(locked_by="editor1")
        session = _make_session(workspace=ws)

        with pytest.raises(InvalidOperationError, match="At least 2"):
            await service.merge_chunks(session, ws.id, user="editor1", chunk_ids=[uuid.uuid4()])

    @pytest.mark.asyncio
    async def test_merge_requires_lock(self, service: ChunkEditorService) -> None:
        """Raises WorkspaceLockError when not locked."""
        ws = _make_workspace(locked_by=None)
        session = _make_session(workspace=ws)

        with pytest.raises(WorkspaceLockError):
            await service.merge_chunks(
                session,
                ws.id,
                user="editor1",
                chunk_ids=[uuid.uuid4(), uuid.uuid4()],
            )


# ---------------------------------------------------------------------------
# TestReorderChunks
# ---------------------------------------------------------------------------


class TestReorderChunks:
    @pytest.mark.asyncio
    async def test_reorder_assigns_new_indices(self, service: ChunkEditorService) -> None:
        """Chunks get new sequential indices in the provided order."""
        ws = _make_workspace(locked_by="editor1")
        c1 = _make_chunk(ws.id, text="A", chunk_index=0)
        c2 = _make_chunk(ws.id, text="B", chunk_index=1)
        session = _make_session(workspace=ws, chunks=[c1, c2])

        result = await service.reorder_chunks(
            session, ws.id, user="editor1", chunk_ids=[c2.id, c1.id]
        )

        assert result[0].chunk_index == 0
        assert result[1].chunk_index == 1
        # First in result should be c2 (was provided first)
        assert result[0] is c2
        assert result[1] is c1

    @pytest.mark.asyncio
    async def test_reorder_requires_lock(self, service: ChunkEditorService) -> None:
        """Raises WorkspaceLockError when not locked."""
        ws = _make_workspace(locked_by=None)
        session = _make_session(workspace=ws)

        with pytest.raises(WorkspaceLockError):
            await service.reorder_chunks(session, ws.id, user="editor1", chunk_ids=[uuid.uuid4()])


# ---------------------------------------------------------------------------
# TestAddChunk
# ---------------------------------------------------------------------------


class TestAddChunk:
    @pytest.mark.asyncio
    async def test_add_chunk_at_end(self, service: ChunkEditorService) -> None:
        """Chunk added without index goes to the end."""
        ws = _make_workspace(locked_by="editor1")
        session = _make_session(workspace=ws)

        chunk = await service.add_chunk(session, ws.id, user="editor1", text="new chunk")

        session.add.assert_called_once()
        session.flush.assert_awaited()
        added = session.add.call_args[0][0]
        assert chunk is added, "add_chunk must return the chunk it staged"
        assert added.text == "new chunk"
        assert added.source == StagedChunkSource.MANUAL
        assert added.status == StagedChunkStatus.DRAFT

    @pytest.mark.asyncio
    async def test_add_chunk_requires_lock(self, service: ChunkEditorService) -> None:
        """Raises WorkspaceLockError when not locked."""
        ws = _make_workspace(locked_by=None)
        session = _make_session(workspace=ws)

        with pytest.raises(WorkspaceLockError):
            await service.add_chunk(session, ws.id, user="editor1", text="text")


# ---------------------------------------------------------------------------
# TestDeleteChunk
# ---------------------------------------------------------------------------


class TestDeleteChunk:
    @pytest.mark.asyncio
    async def test_delete_removes_chunk(self, service: ChunkEditorService) -> None:
        """Chunk is deleted and reindex happens."""
        ws = _make_workspace(locked_by="editor1")
        chunk = _make_chunk(ws.id, text="doomed")
        session = _make_session(workspace=ws, chunks=[chunk])

        await service.delete_chunk(session, ws.id, chunk.id, user="editor1")

        session.delete.assert_awaited_once_with(chunk)

    @pytest.mark.asyncio
    async def test_delete_requires_lock(self, service: ChunkEditorService) -> None:
        """Raises WorkspaceLockError when not locked."""
        ws = _make_workspace(locked_by=None)
        session = _make_session(workspace=ws)

        with pytest.raises(WorkspaceLockError):
            await service.delete_chunk(session, ws.id, uuid.uuid4(), user="editor1")


# ---------------------------------------------------------------------------
# TestApproveReject
# ---------------------------------------------------------------------------


class TestApproveReject:
    @pytest.mark.asyncio
    async def test_approve_sets_status(self, service: ChunkEditorService) -> None:
        """Approve sets chunk status to approved."""
        ws = _make_workspace(locked_by="editor1")
        chunk = _make_chunk(ws.id)
        session = _make_session(workspace=ws, chunks=[chunk])

        result = await service.approve_chunk(
            session, ws.id, chunk.id, user="editor1", reason="Looks good"
        )

        assert result.status == StagedChunkStatus.APPROVED
        assert len(result.edit_history) == 1
        assert result.edit_history[0]["action"] == "approve"

    @pytest.mark.asyncio
    async def test_reject_sets_status(self, service: ChunkEditorService) -> None:
        """Reject sets chunk status to rejected."""
        ws = _make_workspace(locked_by="editor1")
        chunk = _make_chunk(ws.id)
        session = _make_session(workspace=ws, chunks=[chunk])

        result = await service.reject_chunk(
            session, ws.id, chunk.id, user="editor1", reason="Needs work"
        )

        assert result.status == StagedChunkStatus.REJECTED
        assert len(result.edit_history) == 1
        assert result.edit_history[0]["action"] == "reject"

    @pytest.mark.asyncio
    async def test_approve_requires_lock(self, service: ChunkEditorService) -> None:
        """Approve raises WorkspaceLockError when not locked."""
        ws = _make_workspace(locked_by=None)
        session = _make_session(workspace=ws)

        with pytest.raises(WorkspaceLockError):
            await service.approve_chunk(session, ws.id, uuid.uuid4(), user="editor1")

    @pytest.mark.asyncio
    async def test_reject_requires_lock(self, service: ChunkEditorService) -> None:
        """Reject raises WorkspaceLockError when not locked."""
        ws = _make_workspace(locked_by=None)
        session = _make_session(workspace=ws)

        with pytest.raises(WorkspaceLockError):
            await service.reject_chunk(session, ws.id, uuid.uuid4(), user="editor1")
