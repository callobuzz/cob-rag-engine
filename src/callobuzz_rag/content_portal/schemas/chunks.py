"""Pydantic schemas for chunk editor endpoints."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from callobuzz_rag.core.schemas import StrictRequest


class ChunkResponse(BaseModel):
    """Single chunk representation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    staged_document_id: uuid.UUID | None = None
    text: str
    chunk_index: int
    metadata: dict[str, Any] | None = Field(None, alias="metadata_")
    confidence_score: float | None = None
    warnings: dict[str, Any] | None = None
    status: str
    source: str
    edit_history: list[dict[str, Any]] | None = None
    created_at: datetime
    updated_at: datetime


class ChunkListResponse(BaseModel):
    """Paginated chunk list."""

    items: list[ChunkResponse]
    total: int
    offset: int
    limit: int


class EditChunkRequest(StrictRequest):
    """Request to edit a chunk's text or metadata."""

    text: str | None = Field(None, min_length=1, max_length=50000)
    metadata: dict[str, Any] | None = None
    user: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Who is acting, for the audit trail and locks (API keys only: a signed-in user is "
            "always recorded by their account). Defaults to X-User-Id, else 'api_key_user'."
        ),
    )


class SplitChunkRequest(StrictRequest):
    """Request to split a chunk at a character position."""

    position: int = Field(..., ge=1)
    user: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Who is acting, for the audit trail and locks (API keys only: a signed-in user is "
            "always recorded by their account). Defaults to X-User-Id, else 'api_key_user'."
        ),
    )


class SplitChunkResponse(BaseModel):
    """Result of splitting a chunk."""

    first: ChunkResponse
    second: ChunkResponse
    deleted_chunk_id: uuid.UUID


class MergeChunksRequest(StrictRequest):
    """Request to merge multiple chunks into one."""

    chunk_ids: list[uuid.UUID] = Field(..., min_length=2)
    separator: str = Field(default="\n\n", max_length=100)
    user: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Who is acting, for the audit trail and locks (API keys only: a signed-in user is "
            "always recorded by their account). Defaults to X-User-Id, else 'api_key_user'."
        ),
    )


class MergeChunksResponse(BaseModel):
    """Result of merging chunks."""

    merged: ChunkResponse
    deleted_chunk_ids: list[uuid.UUID]


class ReorderChunksRequest(StrictRequest):
    """Request to reorder chunks by providing ordered chunk IDs."""

    chunk_ids: list[uuid.UUID] = Field(..., min_length=1)
    user: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Who is acting, for the audit trail and locks (API keys only: a signed-in user is "
            "always recorded by their account). Defaults to X-User-Id, else 'api_key_user'."
        ),
    )


class AddChunkRequest(StrictRequest):
    """Request to add a new chunk to a workspace."""

    text: str = Field(..., min_length=1, max_length=50000)
    metadata: dict[str, Any] | None = None
    chunk_index: int | None = Field(None, ge=0)
    staged_document_id: uuid.UUID | None = None
    user: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Who is acting, for the audit trail and locks (API keys only: a signed-in user is "
            "always recorded by their account). Defaults to X-User-Id, else 'api_key_user'."
        ),
    )


class ApproveRejectRequest(BaseModel):
    """Request to approve or reject a chunk."""

    user: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Who is acting, for the audit trail and locks (API keys only: a signed-in user is "
            "always recorded by their account). Defaults to X-User-Id, else 'api_key_user'."
        ),
    )
    reason: str | None = Field(None, max_length=1000)
