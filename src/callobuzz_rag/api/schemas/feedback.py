"""Feedback API request/response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from callobuzz_rag.core.schemas import StrictRequest


class FeedbackRequest(StrictRequest):
    """Request body for feedback submission."""

    query_id: UUID = Field(
        ...,
        description="ID of the query to provide feedback on.",
    )
    rating: int = Field(
        ...,
        ge=1,
        le=5,
        description="Rating from 1 (poor) to 5 (excellent).",
    )
    correction: str | None = Field(
        None,
        max_length=5000,
        description="Optional correction or comment.",
    )
    session_id: str | None = Field(
        None,
        max_length=100,
        description="Session ID for grouping related queries.",
    )
    trace_id: str | None = Field(
        None,
        max_length=100,
        description="Langfuse trace ID for observability linking.",
    )


class FeedbackResponse(BaseModel):
    """Response body for feedback submission."""

    feedback_id: UUID
    message: str = "Feedback recorded successfully"


class FeedbackItem(BaseModel):
    """One rating, with the question it was about."""

    id: UUID
    query_id: UUID | None
    corpus_id: UUID | None
    question: str | None = Field(None, description="The question that was answered.")
    rating: int | None
    correction: str | None
    feedback_type: str
    session_id: str | None
    created_at: datetime


class FeedbackListResponse(BaseModel):
    items: list[FeedbackItem]
    total: int
    offset: int
    limit: int


class FeedbackStatsResponse(BaseModel):
    """Aggregated feedback statistics for a corpus."""

    corpus_id: UUID
    total: int = Field(..., description="Total feedback count")
    positive: int = Field(..., description="Ratings >= 4")
    negative: int = Field(..., description="Ratings <= 2")
    corrections: int = Field(..., description="Feedback with corrections")
    implicit: int = Field(..., description="Implicit negative signals")
    start: datetime
    end: datetime
