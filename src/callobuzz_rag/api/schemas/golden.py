"""Golden dataset API request/response schemas."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from callobuzz_rag.api.schemas.evals import (
    Behaviour,
    Difficulty,
    GoldenPairFields,
    LabelName,
)
from callobuzz_rag.conversation.types import LangKey
from callobuzz_rag.core.schemas import StrictRequest


class GoldenPairCreateRequest(GoldenPairFields):
    """Request body for creating a golden pair: a question with an expected answer, an
    expected assistant behaviour, or both (conversation C7), in `set_id` (default: the
    knowledge base's "Golden questions" set)."""

    corpus_id: UUID = Field(..., description="Corpus this pair belongs to.")
    set_id: UUID | None = Field(None, description="Golden set to add it to.")
    expected_chunk_ids: list[UUID] | None = Field(
        None, description="Optional chunk IDs that should be retrieved."
    )
    metadata: dict[str, Any] | None = Field(None, description="Optional metadata.")


class GoldenPairUpdateRequest(BaseModel):
    """Change a golden pair. Only sent fields change; null clears chunk ids or metadata."""

    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(None, min_length=1, max_length=5000)
    expected_answer: str | None = Field(None, min_length=1, max_length=10000)
    expected_chunk_ids: list[UUID] | None = None
    metadata: dict[str, Any] | None = None
    expected_behaviour: Behaviour | None = None
    expected_label: LabelName | None = None
    must_include: list[str] | None = Field(None, max_length=10)
    must_cite: str | None = Field(None, max_length=500)
    language: LangKey | None = None
    page_url: str | None = Field(None, max_length=2048)
    office_hours: Literal["open", "closed"] | None = None
    difficulty: Difficulty | None = None
    topic: str | None = Field(None, max_length=60)


class GoldenPairResponse(BaseModel):
    """Response body for a golden pair."""

    id: UUID
    corpus_id: UUID
    query: str
    expected_answer: str | None = None
    expected_chunk_ids: list[UUID] | None = None
    metadata: dict[str, Any] | None = None
    source: str
    created_at: str
    set_id: UUID | None = None
    expected_behaviour: str | None = None
    expected_label: str | None = None
    must_include: list[str] = Field(default_factory=list)
    must_cite: str | None = None
    origin: str = "human"
    difficulty: str | None = None
    topic: str | None = None


class GoldenPairListResponse(BaseModel):
    """Paginated list of golden pairs."""

    items: list[GoldenPairResponse]
    count: int = Field(..., description="Pairs on this page.")
    total: int = Field(0, description="The corpus's pairs across all pages.")
    offset: int = 0
    limit: int = 50


class GoldenPairDeleteResponse(BaseModel):
    """Response for golden pair deletion."""

    message: str = "Golden pair deleted successfully"


class GoldenImportResponse(BaseModel):
    """Response for CSV import."""

    imported_count: int
    message: str = "Import complete"


class GoldenExportResponse(BaseModel):
    """Response wrapping exported golden pairs."""

    items: list[dict[str, Any]]
    count: int


class GoldenFromFeedbackRequest(StrictRequest):
    """Request to promote a feedback correction to a golden pair."""

    feedback_id: UUID = Field(..., description="ID of the feedback with a correction.")
