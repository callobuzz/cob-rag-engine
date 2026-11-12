"""Unit tests for ReviewWorkflowService — submit, approve, reject, ingest, audit."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.content_portal.services.review_workflow import (
    ChunkValidationError,
    InvalidTransitionError,
    MakerCheckerViolationError,
    ReviewWorkflowError,
    ReviewWorkflowService,
    _validate_transition,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeWorkspace:
    """Minimal workspace fake for testing."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    corpus_id: uuid.UUID = field(default_factory=uuid.uuid4)
    title: str = "Test Workspace"
    status: str = "draft"
    workflow_mode: str = "single_reviewer"
    created_by: str = "creator_user"
    assigned_reviewer: str | None = None
    locked_by: str | None = None
    locked_at: datetime | None = None
    review_feedback: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class FakeChunk:
    """Minimal staged chunk fake for testing."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    text: str = "Chunk text"
    chunk_index: int = 0
    status: str = "draft"
    edit_history: list | None = None


@dataclass
class FakeCorpus:
    """Minimal corpus fake for testing."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    qdrant_collection: str = "test_collection"
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class FakeIngestJob:
    """Minimal ingest job fake for testing."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    corpus_id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: str = "queued"
    mode: str = "new"
    storage_key: str | None = None
    review_metadata: dict | None = None


def _make_session(
    workspace: FakeWorkspace | None = None,
    chunks: list[FakeChunk] | None = None,
    corpus: FakeCorpus | None = None,
) -> AsyncMock:
    """Build an AsyncMock session that returns workspace/chunks/corpus from get()."""
    session = AsyncMock()
    chunks = chunks or []

    async def fake_get(model_cls, obj_id):
        cls_name = model_cls.__name__ if hasattr(model_cls, "__name__") else str(model_cls)
        if cls_name == "Workspace":
            if workspace is not None and workspace.id == obj_id:
                return workspace
            return None
        if cls_name == "Corpus":
            return corpus
        return None

    session.get = AsyncMock(side_effect=fake_get)
    session.add = MagicMock()
    session.flush = AsyncMock()

    # For execute() — handle both count-by-status and chunk list queries
    call_count = {"n": 0}

    def _make_count_result():
        """Return grouped status counts."""
        from collections import Counter

        status_counts = Counter(c.status for c in chunks)
        mock_result = MagicMock()
        mock_result.all.return_value = list(status_counts.items())
        return mock_result

    def _make_chunk_result():
        """Return chunk list as scalars."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = chunks
        return mock_result

    async def fake_execute(stmt):
        call_count["n"] += 1
        # Inspect the statement to decide what to return
        stmt_str = str(stmt)
        if "count" in stmt_str.lower() or "COUNT" in stmt_str:
            return _make_count_result()
        return _make_chunk_result()

    session.execute = AsyncMock(side_effect=fake_execute)

    return session


@pytest.fixture
def service() -> ReviewWorkflowService:
    return ReviewWorkflowService()


@pytest.fixture(autouse=True)
def upload_room():  # type: ignore[no-untyped-def]
    """The fair-use upload check and count (PIP3) that ingesting a workspace makes, spied on."""
    from types import SimpleNamespace
    from unittest.mock import patch

    with (
        patch("callobuzz_rag.accounts.caps.check_upload_room", AsyncMock()) as check,
        patch("callobuzz_rag.fairuse.counters.bump", AsyncMock()) as bump,
    ):
        yield SimpleNamespace(check=check, bump=bump)


# ---------------------------------------------------------------------------
# TestValidateTransition
# ---------------------------------------------------------------------------


class TestValidateTransition:
    """Tests for the _validate_transition helper."""

    def test_draft_to_in_review(self) -> None:
        _validate_transition("draft", "in_review")

    def test_in_review_to_approved(self) -> None:
        _validate_transition("in_review", "approved")

    def test_in_review_to_draft(self) -> None:
        _validate_transition("in_review", "draft")

    def test_approved_to_ingested(self) -> None:
        _validate_transition("approved", "ingested")

    def test_draft_to_approved_invalid(self) -> None:
        with pytest.raises(InvalidTransitionError):
            _validate_transition("draft", "approved")

    def test_ingested_to_anything_invalid(self) -> None:
        with pytest.raises(InvalidTransitionError):
            _validate_transition("ingested", "draft")

    def test_archived_to_anything_invalid(self) -> None:
        with pytest.raises(InvalidTransitionError):
            _validate_transition("archived", "in_review")

    def test_unknown_status_raises(self) -> None:
        with pytest.raises(InvalidTransitionError):
            _validate_transition("nonexistent", "draft")


# ---------------------------------------------------------------------------
# TestSubmitForReview
# ---------------------------------------------------------------------------


class TestSubmitForReview:
    @pytest.mark.asyncio
    async def test_submit_happy_path(self, service: ReviewWorkflowService) -> None:
        """Draft workspace with chunks transitions to in_review."""
        ws = FakeWorkspace(status="draft", locked_by="someone")
        chunks = [FakeChunk(workspace_id=ws.id, status="draft")]
        session = _make_session(workspace=ws, chunks=chunks)

        result = await service.submit_for_review(session, ws.id, user="user1")

        assert result["status"] == "in_review"
        assert result["total_chunks"] == 1
        assert ws.status == "in_review"
        assert ws.locked_by is None

    @pytest.mark.asyncio
    async def test_submit_not_draft_raises(self, service: ReviewWorkflowService) -> None:
        """Cannot submit a workspace that is not in draft status."""
        ws = FakeWorkspace(status="approved")
        session = _make_session(workspace=ws)

        with pytest.raises(InvalidTransitionError):
            await service.submit_for_review(session, ws.id, user="user1")

    @pytest.mark.asyncio
    async def test_submit_empty_workspace_raises(self, service: ReviewWorkflowService) -> None:
        """Cannot submit a workspace with no chunks."""
        ws = FakeWorkspace(status="draft")
        session = _make_session(workspace=ws, chunks=[])

        with pytest.raises(ChunkValidationError, match="empty workspace"):
            await service.submit_for_review(session, ws.id, user="user1")

    @pytest.mark.asyncio
    async def test_submit_rejected_chunks_raises(self, service: ReviewWorkflowService) -> None:
        """Cannot submit workspace with rejected chunks."""
        ws = FakeWorkspace(status="draft")
        chunks = [
            FakeChunk(workspace_id=ws.id, status="draft"),
            FakeChunk(workspace_id=ws.id, status="rejected"),
        ]
        session = _make_session(workspace=ws, chunks=chunks)

        with pytest.raises(ChunkValidationError, match="rejected"):
            await service.submit_for_review(session, ws.id, user="user1")

    @pytest.mark.asyncio
    async def test_submit_not_found_raises(self, service: ReviewWorkflowService) -> None:
        """Submitting a non-existent workspace raises."""
        session = _make_session(workspace=None)

        with pytest.raises(ReviewWorkflowError, match="not found"):
            await service.submit_for_review(session, uuid.uuid4(), user="user1")


# ---------------------------------------------------------------------------
# TestApproveWorkspace
# ---------------------------------------------------------------------------


class TestApproveWorkspace:
    @pytest.mark.asyncio
    async def test_approve_in_review(self, service: ReviewWorkflowService) -> None:
        """Workspace in_review transitions to approved."""
        ws = FakeWorkspace(status="in_review")
        session = _make_session(workspace=ws)

        result = await service.approve_workspace(
            session, ws.id, user="reviewer", comment="Looks good"
        )

        assert result["status"] == "approved"
        assert result["review_feedback"] == "Looks good"
        assert ws.status == "approved"

    @pytest.mark.asyncio
    async def test_approve_single_reviewer_same_user(self, service: ReviewWorkflowService) -> None:
        """In single_reviewer mode, creator can approve their own workspace."""
        ws = FakeWorkspace(
            status="in_review",
            workflow_mode="single_reviewer",
            created_by="user1",
        )
        session = _make_session(workspace=ws)

        result = await service.approve_workspace(session, ws.id, user="user1")

        assert result["status"] == "approved"

    @pytest.mark.asyncio
    async def test_approve_maker_checker_same_user_raises(
        self, service: ReviewWorkflowService
    ) -> None:
        """In maker_checker mode, creator cannot approve."""
        ws = FakeWorkspace(
            status="in_review",
            workflow_mode="maker_checker",
            created_by="user1",
        )
        session = _make_session(workspace=ws)

        with pytest.raises(MakerCheckerViolationError):
            await service.approve_workspace(session, ws.id, user="user1")

    @pytest.mark.asyncio
    async def test_approve_maker_checker_different_user(
        self, service: ReviewWorkflowService
    ) -> None:
        """In maker_checker mode, a different user can approve."""
        ws = FakeWorkspace(
            status="in_review",
            workflow_mode="maker_checker",
            created_by="creator",
        )
        session = _make_session(workspace=ws)

        result = await service.approve_workspace(session, ws.id, user="reviewer")

        assert result["status"] == "approved"

    @pytest.mark.asyncio
    async def test_approve_not_in_review_raises(self, service: ReviewWorkflowService) -> None:
        """Cannot approve workspace that is not in_review."""
        ws = FakeWorkspace(status="draft")
        session = _make_session(workspace=ws)

        with pytest.raises(InvalidTransitionError):
            await service.approve_workspace(session, ws.id, user="reviewer")


# ---------------------------------------------------------------------------
# TestRejectWorkspace
# ---------------------------------------------------------------------------


class TestRejectWorkspace:
    @pytest.mark.asyncio
    async def test_reject_with_feedback(self, service: ReviewWorkflowService) -> None:
        """Rejecting returns workspace to draft with feedback."""
        ws = FakeWorkspace(status="in_review")
        session = _make_session(workspace=ws)

        result = await service.reject_workspace(
            session, ws.id, user="reviewer", feedback="Needs rework"
        )

        assert result["status"] == "draft"
        assert result["review_feedback"] == "Needs rework"
        assert ws.status == "draft"
        assert ws.review_feedback == "Needs rework"

    @pytest.mark.asyncio
    async def test_reject_not_in_review_raises(self, service: ReviewWorkflowService) -> None:
        """Cannot reject workspace that is not in_review."""
        ws = FakeWorkspace(status="approved")
        session = _make_session(workspace=ws)

        with pytest.raises(InvalidTransitionError):
            await service.reject_workspace(session, ws.id, user="reviewer", feedback="No")

    @pytest.mark.asyncio
    async def test_reject_stores_feedback(self, service: ReviewWorkflowService) -> None:
        """Feedback is stored on the workspace model."""
        ws = FakeWorkspace(status="in_review")
        session = _make_session(workspace=ws)

        await service.reject_workspace(session, ws.id, user="reviewer", feedback="Fix section 3")

        assert ws.review_feedback == "Fix section 3"


# ---------------------------------------------------------------------------
# TestIngestWorkspace
# ---------------------------------------------------------------------------


class TestIngestWorkspace:
    @pytest.mark.asyncio
    async def test_ingest_approved_workspace(self, service: ReviewWorkflowService) -> None:
        """Ingesting approved workspace uploads to MinIO and dispatches task."""
        import sys

        ws = FakeWorkspace(status="approved")
        corpus = FakeCorpus(id=ws.corpus_id)
        chunks = [
            FakeChunk(workspace_id=ws.id, text="Chunk 1", status="approved"),
            FakeChunk(workspace_id=ws.id, text="Chunk 2", status="draft"),
        ]
        session = _make_session(workspace=ws, chunks=chunks, corpus=corpus)

        mock_storage = AsyncMock()
        mock_settings = MagicMock()
        mock_settings.s3_bucket = "test-bucket"
        mock_task = MagicMock()
        mock_task.delay = MagicMock()

        # Pre-seed sys.modules to prevent Celery app initialization
        fake_tasks_mod = MagicMock()
        fake_tasks_mod.ingest_document_task = mock_task
        fake_s3_mod = MagicMock()
        fake_s3_mod.object_storage = MagicMock(return_value=mock_storage)
        fake_config_mod = MagicMock()
        fake_config_mod.Settings = MagicMock(return_value=mock_settings)
        fake_key_mod = MagicMock()
        fake_key_mod.build_raw_document_key = MagicMock(return_value="test/key")

        saved = {}
        mods_to_patch = {
            "callobuzz_rag.worker.tasks": fake_tasks_mod,
            "callobuzz_rag.storage.factory": fake_s3_mod,
            "callobuzz_rag.core.config": fake_config_mod,
            "callobuzz_rag.storage.key_builder": fake_key_mod,
        }
        for mod_name, fake_mod in mods_to_patch.items():
            saved[mod_name] = sys.modules.get(mod_name)
            sys.modules[mod_name] = fake_mod

        try:
            result = await service.ingest_workspace(session, ws.id, user="ingester")
        finally:
            for mod_name, orig in saved.items():
                if orig is None:
                    sys.modules.pop(mod_name, None)
                else:
                    sys.modules[mod_name] = orig

        assert result["status"] == "ingested"
        assert result["chunks_ingested"] == 2
        assert ws.status == "ingested"
        mock_storage.upload.assert_awaited_once()
        # The route queues the task after its commit (the worker must find the job row).
        mock_task.delay.assert_not_called()
        assert result["dispatch"]["storage_key"] == "test/key"
        assert result["dispatch"]["job_id"] == str(result["job_id"])

    @pytest.mark.asyncio
    async def test_ingest_counts_one_upload_of_the_package_bytes(
        self, service: ReviewWorkflowService, upload_room
    ) -> None:
        """FU-4: the portal's packaged document is stored like an upload, so it is checked
        against the storage and day limits, counted, and its job keeps the bytes."""
        from unittest.mock import patch

        ws = FakeWorkspace(status="approved")
        corpus = FakeCorpus(id=ws.corpus_id)
        chunks = [FakeChunk(workspace_id=ws.id, text="Chunk 1", status="approved")]
        session = _make_session(workspace=ws, chunks=chunks, corpus=corpus)
        with patch("callobuzz_rag.storage.factory.object_storage", return_value=AsyncMock()):
            await service.ingest_workspace(session, ws.id, user="ingester")
        size = len(b"Chunk 1")
        upload_room.check.assert_awaited_once()
        assert upload_room.check.await_args.args[1:3] == (corpus.tenant_id, size)
        upload_room.bump.assert_awaited_once_with(session, corpus.tenant_id, "uploads", 1)
        job = session.add.call_args.args[0]
        assert job.review_metadata["size_bytes"] == size

    @pytest.mark.asyncio
    async def test_ingest_past_the_upload_limit_stores_nothing(
        self, service: ReviewWorkflowService, upload_room
    ) -> None:
        from unittest.mock import patch

        from callobuzz_rag.api.errors import APIError

        ws = FakeWorkspace(status="approved")
        corpus = FakeCorpus(id=ws.corpus_id)
        chunks = [FakeChunk(workspace_id=ws.id, text="Chunk 1", status="approved")]
        session = _make_session(workspace=ws, chunks=chunks, corpus=corpus)
        upload_room.check.side_effect = APIError(409, "full", "storage_limit_reached")
        storage = AsyncMock()
        with (
            patch("callobuzz_rag.storage.factory.object_storage", return_value=storage),
            pytest.raises(APIError) as err,
        ):
            await service.ingest_workspace(session, ws.id, user="ingester")
        assert err.value.code == "storage_limit_reached"
        session.add.assert_not_called()
        storage.upload.assert_not_called()
        assert ws.status == "approved"

    @pytest.mark.asyncio
    async def test_ingest_not_approved_raises(self, service: ReviewWorkflowService) -> None:
        """Cannot ingest workspace that is not approved."""
        ws = FakeWorkspace(status="in_review")
        session = _make_session(workspace=ws)

        with pytest.raises(InvalidTransitionError):
            await service.ingest_workspace(session, ws.id, user="user1")

    @pytest.mark.asyncio
    async def test_ingest_all_rejected_raises(self, service: ReviewWorkflowService) -> None:
        """Cannot ingest when all chunks are rejected."""
        ws = FakeWorkspace(status="approved")
        corpus = FakeCorpus(id=ws.corpus_id)
        # Empty list since the query filters out rejected chunks
        session = _make_session(workspace=ws, chunks=[], corpus=corpus)

        mock_settings = MagicMock()
        mock_settings.s3_bucket = "test-bucket"

        with pytest.raises(ChunkValidationError, match="all chunks are rejected"):
            await service.ingest_workspace(session, ws.id, user="user1")

    @pytest.mark.asyncio
    async def test_ingest_not_found_raises(self, service: ReviewWorkflowService) -> None:
        """Ingesting a non-existent workspace raises."""
        session = _make_session(workspace=None)

        with pytest.raises(ReviewWorkflowError, match="not found"):
            await service.ingest_workspace(session, uuid.uuid4(), user="user1")


# ---------------------------------------------------------------------------
# TestGetAuditTrail
# ---------------------------------------------------------------------------


class TestGetAuditTrail:
    @pytest.mark.asyncio
    async def test_aggregates_history(self, service: ReviewWorkflowService) -> None:
        """Audit trail collects edit_history from all chunks, newest first."""
        ws = FakeWorkspace()
        c1 = FakeChunk(
            workspace_id=ws.id,
            edit_history=[
                {
                    "timestamp": "2026-03-09T10:00:00+00:00",
                    "user": "editor1",
                    "action": "edit",
                    "old_text": "old",
                    "new_text": "new",
                },
            ],
        )
        c2 = FakeChunk(
            workspace_id=ws.id,
            edit_history=[
                {
                    "timestamp": "2026-03-09T11:00:00+00:00",
                    "user": "editor2",
                    "action": "edit",
                    "old_text": "x",
                    "new_text": "y",
                },
            ],
        )
        session = _make_session(workspace=ws, chunks=[c1, c2])

        entries, total = await service.get_audit_trail(session, ws.id)

        assert total == 2
        assert len(entries) == 2
        # Newest first
        assert entries[0]["user"] == "editor2"
        assert entries[1]["user"] == "editor1"
        # chunk_id is enriched
        assert entries[0]["chunk_id"] == c2.id
        assert entries[1]["chunk_id"] == c1.id

    @pytest.mark.asyncio
    async def test_pagination(self, service: ReviewWorkflowService) -> None:
        """Audit trail respects offset and limit."""
        ws = FakeWorkspace()
        c = FakeChunk(
            workspace_id=ws.id,
            edit_history=[
                {
                    "timestamp": f"2026-03-09T{10 + i:02d}:00:00+00:00",
                    "user": f"user{i}",
                    "action": "edit",
                }
                for i in range(5)
            ],
        )
        session = _make_session(workspace=ws, chunks=[c])

        entries, total = await service.get_audit_trail(session, ws.id, offset=1, limit=2)

        assert total == 5
        assert len(entries) == 2

    @pytest.mark.asyncio
    async def test_every_stored_shape_fits_the_response(
        self, service: ReviewWorkflowService
    ) -> None:
        """Object and string `details`, and the AI chat's old timestamp-less entries with
        top-level extras, all serialise (the object form used to answer 500)."""
        from callobuzz_rag.content_portal.schemas.review import AuditTrailEntry
        from callobuzz_rag.content_portal.services.chunk_editor import _make_history_entry

        ws = FakeWorkspace()
        source = str(uuid.uuid4())
        c = FakeChunk(
            workspace_id=ws.id,
            edit_history=[
                _make_history_entry("u", "split", details={"position": 3, "source_chunk_id": "x"}),
                _make_history_entry("u", "approve", details={"reason": "ok"}),
                {"user": "ai", "action": "split_from", "source_chunk_id": source},
                {
                    "user": "old",
                    "action": "note",
                    "timestamp": "2026-03-09T10:00:00+00:00",
                    "details": "plain",
                },
                {"user": "odd", "action": "note", "details": ["a", "b"]},
                "not an entry",
            ],
        )
        session = _make_session(workspace=ws, chunks=[c])

        entries, total = await service.get_audit_trail(session, ws.id)

        assert total == 5
        parsed = {e.action + e.user: e for e in (AuditTrailEntry(**x) for x in entries)}
        assert parsed["splitu"].details == {"position": 3, "source_chunk_id": "x"}
        assert parsed["approveu"].details == {"reason": "ok"}
        assert parsed["split_fromai"].details == {"source_chunk_id": source}
        assert parsed["split_fromai"].timestamp is None
        assert parsed["noteold"].details == "plain"
        assert parsed["noteodd"].details == {"value": ["a", "b"]}
        # Entries without a timestamp sort last.
        assert entries[-1]["timestamp"] is None

    @pytest.mark.asyncio
    async def test_empty_workspace(self, service: ReviewWorkflowService) -> None:
        """Workspace with no chunks returns empty audit trail."""
        ws = FakeWorkspace()
        session = _make_session(workspace=ws, chunks=[])

        entries, total = await service.get_audit_trail(session, ws.id)

        assert total == 0
        assert entries == []

    @pytest.mark.asyncio
    async def test_not_found_raises(self, service: ReviewWorkflowService) -> None:
        """Audit trail for non-existent workspace raises."""
        session = _make_session(workspace=None)

        with pytest.raises(ReviewWorkflowError, match="not found"):
            await service.get_audit_trail(session, uuid.uuid4())
