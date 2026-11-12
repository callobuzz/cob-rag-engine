"""Ingest API schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from callobuzz_rag.core.schemas import StrictRequest


class SimilarDocumentInfo(BaseModel):
    """Info about a document that appears similar to the uploaded file."""

    document_id: UUID
    similarity: float
    match_count: int
    file_name: str | None = Field(None, description="The existing document's file name.")


class ConfirmReviewRequest(StrictRequest):
    """Request body for confirming a pending review ingest job."""

    action: str  # "replace", "new" or "skip" ("discard" is accepted as "skip")
    target_document_id: UUID | None = None

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v == "discard":
            return "skip"
        if v not in ("replace", "new", "skip"):
            raise ValueError(f"action must be 'replace', 'new' or 'skip', got '{v}'")
        return v


class IngestResponse(BaseModel):
    job_id: UUID
    status: str
    message: str = "Document accepted for processing"
    # Phase 6: Version tracking
    document_id: UUID | None = None
    version_number: int | None = None
    replaced_document_id: UUID | None = None
    similar_documents: list[SimilarDocumentInfo] | None = None


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: str
    corpus_id: UUID | None = None
    file_name: str | None = Field(None, description="The uploaded file (none for records).")
    mode: str | None = Field(
        None, description="auto | new | replace | version, or records for a records import."
    )
    target_document_id: UUID | None = None
    total_documents: int | None
    processed_documents: int
    errors: dict[str, Any] | None
    created_at: datetime
    finished_at: datetime | None
    expires_at: datetime | None = Field(
        None, description="When a pending_review job stops accepting a decision."
    )
    similar_documents: list[SimilarDocumentInfo] | None = Field(
        None,
        description="For pending_review: the existing documents this upload resembles.",
    )
    records: dict[str, int] | None = None
    retried: bool = Field(
        False,
        description="A failed upload that a newer job already runs again (or finished); "
        "retrying it again is refused with `already_retried`.",
    )


class JobListResponse(BaseModel):
    items: list[JobStatusResponse]
    count: int = Field(..., description="Items on this page.")
    total: int = Field(0, description="Jobs matching the filters, across all pages.")
    offset: int = 0
    limit: int = 20


MAX_RECORDS_PER_REQUEST = 1000
MAX_RECORD_TEXT_CHARS = 200_000


class RecordInput(StrictRequest):
    """One piece of already-text content: an FAQ entry, product listing, help article."""

    id: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Your id for this record. Importing the same id again replaces it.",
    )
    text: str = Field(..., min_length=1, max_length=MAX_RECORD_TEXT_CHARS)
    title: str | None = Field(None, max_length=1000)
    url: str | None = Field(None, max_length=2000)
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Filterable metadata; `category` and `user_id` have their own filters.",
    )

    @field_validator("id")
    @classmethod
    def not_reserved(cls, value: str) -> str:
        if value.startswith("system:"):
            raise ValueError("ids starting with 'system:' are reserved for the engine")
        if value.startswith(("article:", "faq:")):
            raise ValueError(
                "ids starting with 'article:' or 'faq:' are reserved for articles "
                "written in the dashboard"
            )
        return value


class IngestRecordsRequest(StrictRequest):
    corpus_id: UUID | None = None
    records: list[RecordInput] = Field(..., min_length=1, max_length=MAX_RECORDS_PER_REQUEST)

    @field_validator("records")
    @classmethod
    def unique_ids(cls, v: list[RecordInput]) -> list[RecordInput]:
        seen: set[str] = set()
        for record in v:
            if record.id in seen:
                raise ValueError(f"duplicate record id in request: {record.id!r}")
            seen.add(record.id)
        return v


class IngestRecordsResponse(BaseModel):
    job_id: UUID
    status: str
    records: int
