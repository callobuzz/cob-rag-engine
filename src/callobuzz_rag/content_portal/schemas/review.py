"""Request/response schemas for review workflow endpoints."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from callobuzz_rag.core.schemas import StrictRequest


class SubmitForReviewRequest(BaseModel):
    """Request body for submitting a workspace for review."""

    user: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Who is acting, for the audit trail and locks (API keys only: a signed-in user is "
            "always recorded by their account). Defaults to X-User-Id, else 'api_key_user'."
        ),
    )


class SubmitForReviewResponse(BaseModel):
    """Response after submitting a workspace for review."""

    workspace_id: uuid.UUID
    status: str
    message: str
    total_chunks: int
    approved_chunks: int
    rejected_chunks: int
    draft_chunks: int


class ApproveWorkspaceRequest(BaseModel):
    """Request body for approving a workspace."""

    user: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Who is acting, for the audit trail and locks (API keys only: a signed-in user is "
            "always recorded by their account). Defaults to X-User-Id, else 'api_key_user'."
        ),
    )
    comment: str | None = Field(default=None, max_length=2000)


class RejectWorkspaceRequest(StrictRequest):
    """Request body for rejecting a workspace."""

    user: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Who is acting, for the audit trail and locks (API keys only: a signed-in user is "
            "always recorded by their account). Defaults to X-User-Id, else 'api_key_user'."
        ),
    )
    feedback: str = Field(..., min_length=1, max_length=5000)


class WorkspaceStatusResponse(BaseModel):
    """Response after approving or rejecting a workspace."""

    workspace_id: uuid.UUID
    status: str
    message: str
    review_feedback: str | None = None
    updated_at: datetime | None = None


class IngestWorkspaceRequest(BaseModel):
    """Request body for ingesting an approved workspace."""

    user: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Who is acting, for the audit trail and locks (API keys only: a signed-in user is "
            "always recorded by their account). Defaults to X-User-Id, else 'api_key_user'."
        ),
    )


class IngestWorkspaceResponse(BaseModel):
    """Response after triggering workspace ingestion."""

    workspace_id: uuid.UUID
    status: str
    message: str
    chunks_ingested: int
    job_id: uuid.UUID | None = None


class AuditTrailEntry(BaseModel):
    """Single entry in the workspace audit trail."""

    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime | None = Field(
        default=None, description="When it happened; null on entries recorded without one."
    )
    user: str
    action: str
    details: dict[str, Any] | str | None = Field(
        default=None,
        description=(
            "What the action recorded, as an object: split {position, source_chunk_id}, "
            "merge {source_chunk_ids}, approve / reject {reason}, edit_metadata {metadata}. "
            "A plain string on older entries."
        ),
    )
    old_text: str | None = None
    new_text: str | None = None
    chunk_id: uuid.UUID | None = None


class AuditTrailResponse(BaseModel):
    """Paginated audit trail for a workspace."""

    workspace_id: uuid.UUID
    entries: list[AuditTrailEntry]
    total: int
