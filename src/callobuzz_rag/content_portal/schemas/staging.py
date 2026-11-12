"""Request/response schemas for content staging endpoints."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContentUploadRequest(BaseModel):
    """Request body for text/URL content upload (non-file uploads)."""

    source_type: str = Field(
        ...,
        description="Content source type: 'text_dump' or 'url'",
    )
    content: str = Field(
        ...,
        min_length=1,
        max_length=500_000,
        description="Raw text content or URL string",
    )
    title: str | None = Field(
        None,
        max_length=500,
        description="Optional title for the content",
    )

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, v: str) -> str:
        allowed = {"text_dump", "url"}
        if v not in allowed:
            raise ValueError(f"source_type must be one of {sorted(allowed)}, got '{v}'")
        return v

    @field_validator("content")
    @classmethod
    def validate_url_content(cls, v: str, info: Any) -> str:
        """If source_type is 'url', validate it looks like a URL."""
        source_type = info.data.get("source_type")
        if source_type == "url" and not v.startswith(("http://", "https://")):
            raise ValueError("URL content must start with http:// or https://")
        return v


class StagedContentResponse(BaseModel):
    """Response schema for a single staged content item."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    source_type: str
    original_filename: str | None
    mime_type: str | None
    raw_storage_key: str
    ai_status: str
    ai_output_type: str | None
    ai_quality_report: dict[str, Any] | None
    token_usage: dict[str, Any] | None
    created_at: datetime


class StagedContentListResponse(BaseModel):
    """Response for listing staged content in a workspace."""

    items: list[StagedContentResponse]
    count: int


class ReprocessRequest(BaseModel):
    """Request body for re-running AI transformation."""

    force: bool = Field(
        False,
        description="Force reprocessing even if already completed",
    )
    model_override: str | None = Field(
        None,
        description="Override the LLM model for this reprocessing run",
    )


class ReprocessResponse(BaseModel):
    """Response after triggering reprocessing."""

    content_id: UUID
    ai_status: str
    message: str
