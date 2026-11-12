"""Document management schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentSummary(BaseModel):
    """Summary of a document for listing."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    file_name: str | None
    mime_type: str | None
    size_bytes: int | None
    ingested_at: datetime
    active_version_id: UUID | None = None
    version_count: int = 0
    external_id: str | None = Field(None, description="Your id, for a record import.")
    metadata: dict[str, Any] | None = Field(None, description="Metadata given at ingest.")


class DocumentListResponse(BaseModel):
    items: list[DocumentSummary]
    count: int = Field(..., description="Documents matching the filter, across all pages.")


class VersionSummary(BaseModel):
    """Summary of a document version."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    chunk_count: int = 0
    parser_version: str | None
    chunk_schema_version: str | None
    created_at: datetime


class VersionListResponse(BaseModel):
    document_id: UUID
    file_name: str | None
    versions: list[VersionSummary]
    count: int


class ChunkView(BaseModel):
    """One chunk as stored, read-only."""

    id: UUID
    chunk_index: int | None
    text: str
    section: list[str] | None = Field(None, description="Heading path the chunk sits under.")
    page: int | None
    token_count: int | None
    is_table: bool
    is_code: bool
    parent_chunk_id: UUID | None = None


class DocumentDetailResponse(BaseModel):
    """A document, its versions, and one version's chunks (paginated)."""

    id: UUID
    file_name: str | None
    mime_type: str | None
    size_bytes: int | None
    ingested_at: datetime
    external_id: str | None
    metadata: dict[str, Any] | None
    source_uri: str | None
    active_version_id: UUID | None
    versions: list[VersionSummary]
    version_id: UUID | None = Field(None, description="The version whose chunks are shown.")
    chunks: list[ChunkView]
    chunk_total: int
    chunk_offset: int
    chunk_limit: int


class MergeResponse(BaseModel):
    status: str = "merged"
    old_document_id: UUID
    new_document_id: UUID
    versions_preserved: int


class RestoreResponse(BaseModel):
    status: str = "restored"
    document_id: UUID
    restored_version_id: UUID
    previous_active_version_id: UUID | None
