"""Unit tests for workspace request/response schemas."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from callobuzz_rag.content_portal.schemas.workspaces import (
    WorkspaceCreate,
    WorkspaceLock,
    WorkspaceResponse,
    WorkspaceUpdate,
)


class TestWorkspaceCreate:
    def test_valid_minimal(self) -> None:
        """Minimal valid creation request."""
        schema = WorkspaceCreate(title="My Workspace")
        assert schema.title == "My Workspace"
        assert schema.workflow_mode == "single_reviewer"
        assert schema.assigned_reviewer is None

    def test_valid_full(self) -> None:
        """Full creation request with all fields."""
        schema = WorkspaceCreate(
            title="Full Workspace",
            workflow_mode="maker_checker",
            assigned_reviewer="reviewer@example.com",
        )
        assert schema.workflow_mode == "maker_checker"
        assert schema.assigned_reviewer == "reviewer@example.com"

    def test_empty_title_rejected(self) -> None:
        """Empty title is rejected."""
        with pytest.raises(ValidationError, match="String should have at least 1"):
            WorkspaceCreate(title="")

    def test_title_too_long_rejected(self) -> None:
        """Title exceeding 500 chars is rejected."""
        with pytest.raises(ValidationError):
            WorkspaceCreate(title="x" * 501)

    def test_invalid_workflow_mode_rejected(self) -> None:
        """Invalid workflow mode is rejected."""
        with pytest.raises(ValidationError, match="workflow_mode must be one of"):
            WorkspaceCreate(title="Test", workflow_mode="invalid_mode")

    def test_single_reviewer_accepted(self) -> None:
        """single_reviewer is a valid workflow mode."""
        schema = WorkspaceCreate(title="Test", workflow_mode="single_reviewer")
        assert schema.workflow_mode == "single_reviewer"

    def test_maker_checker_accepted(self) -> None:
        """maker_checker is a valid workflow mode."""
        schema = WorkspaceCreate(title="Test", workflow_mode="maker_checker")
        assert schema.workflow_mode == "maker_checker"


class TestWorkspaceUpdate:
    def test_all_none_has_no_changes(self) -> None:
        """Update with no fields set reports no changes."""
        schema = WorkspaceUpdate()
        assert schema.has_changes() is False

    def test_title_set_has_changes(self) -> None:
        """Update with title set reports changes."""
        schema = WorkspaceUpdate(title="New Title")
        assert schema.has_changes() is True

    def test_reviewer_set_has_changes(self) -> None:
        """Update with reviewer set reports changes."""
        schema = WorkspaceUpdate(assigned_reviewer="new@example.com")
        assert schema.has_changes() is True


class TestWorkspaceLock:
    def test_valid_lock(self) -> None:
        """Valid lock request."""
        schema = WorkspaceLock(user="editor@example.com")
        assert schema.user == "editor@example.com"

    def test_empty_user_rejected(self) -> None:
        """Empty user string is rejected."""
        with pytest.raises(ValidationError, match="String should have at least 1"):
            WorkspaceLock(user="")

    def test_user_too_long_rejected(self) -> None:
        """User exceeding 255 chars is rejected."""
        with pytest.raises(ValidationError):
            WorkspaceLock(user="x" * 256)


class TestWorkspaceResponse:
    def test_from_attributes(self) -> None:
        """WorkspaceResponse can be created from ORM attributes."""
        now = datetime.now(UTC)
        ws_id = uuid.uuid4()
        c_id = uuid.uuid4()

        fake = type(
            "FakeWorkspace",
            (),
            {
                "id": ws_id,
                "corpus_id": c_id,
                "title": "Test WS",
                "status": "draft",
                "workflow_mode": "single_reviewer",
                "created_by": "user1",
                "assigned_reviewer": None,
                "locked_by": None,
                "locked_at": None,
                "review_feedback": None,
                "created_at": now,
                "updated_at": now,
            },
        )()

        response = WorkspaceResponse.model_validate(fake)
        assert response.id == ws_id
        assert response.corpus_id == c_id
        assert response.status == "draft"
