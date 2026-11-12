"""Request/response schemas for workspace endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from callobuzz_rag.core.schemas import StrictRequest


class WorkspaceCreate(StrictRequest):
    """Request body for creating a workspace."""

    title: str = Field(..., min_length=1, max_length=500)
    workflow_mode: str = Field(default="single_reviewer")
    assigned_reviewer: str | None = Field(default=None, max_length=255)

    @field_validator("workflow_mode")
    @classmethod
    def validate_workflow_mode(cls, v: str) -> str:
        allowed = {"single_reviewer", "maker_checker"}
        if v not in allowed:
            msg = f"workflow_mode must be one of: {', '.join(sorted(allowed))}"
            raise ValueError(msg)
        return v

    @field_validator("assigned_reviewer")
    @classmethod
    def validate_reviewer_for_maker_checker(cls, v: str | None, info: object) -> str | None:
        """Warn but allow — reviewer can be assigned later."""
        return v


class WorkspaceUpdate(StrictRequest):
    """Request body for updating a workspace."""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    assigned_reviewer: str | None = Field(default=None, max_length=255)

    def has_changes(self) -> bool:
        """Check if any fields are set (not None)."""
        return any(v is not None for v in [self.title, self.assigned_reviewer])


class WorkspaceLock(BaseModel):
    """Request body for acquiring a workspace lock."""

    user: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Who is acting, for the audit trail and locks (API keys only: a signed-in user is "
            "always recorded by their account). Defaults to X-User-Id, else 'api_key_user'."
        ),
    )


class WorkspaceResponse(BaseModel):
    """Workspace detail response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    corpus_id: uuid.UUID
    title: str
    kind: str = Field(
        default="standard",
        description=(
            "standard (authoring) or post_ingest_review (created from an ingested document; "
            "published with finalize-review)."
        ),
    )
    reviewed_version_id: uuid.UUID | None = Field(
        default=None, description="post_ingest_review: the document version under review."
    )
    finalized_version_id: uuid.UUID | None = Field(
        default=None, description="post_ingest_review: the version finalizing produced."
    )
    status: str
    workflow_mode: str
    created_by: str
    assigned_reviewer: str | None
    locked_by: str | None
    locked_at: datetime | None
    review_feedback: str | None
    created_at: datetime
    updated_at: datetime
    # Stats (populated by service, not from ORM)
    content_count: int | None = None
    chunk_count: int | None = None
    approved_chunk_count: int | None = None


class WorkspaceListResponse(BaseModel):
    """Paginated list of workspaces."""

    items: list[WorkspaceResponse]
    total: int
    offset: int
    limit: int


class WorkspaceSummary(BaseModel):
    """Lightweight workspace info for list views."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    status: str
    created_by: str
    locked_by: str | None
    created_at: datetime
    updated_at: datetime
