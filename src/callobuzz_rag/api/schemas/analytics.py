"""Analytics API request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class QueryCountResponse(BaseModel):
    """Total query count."""

    count: int


class TrendPoint(BaseModel):
    """Single data point in a time-series trend."""

    bucket: datetime
    count: int
    avg_confidence: float | None = None
    avg_latency: float | None = None


class TrendsResponse(BaseModel):
    """Time-series trend data."""

    data: list[TrendPoint]


class QueryHistoryItem(BaseModel):
    """Single query log entry for history listing."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    query_text: str | None = None
    response: str | None = None
    confidence_score: float | None = None
    latency_ms: int | None = None
    corpus_id: uuid.UUID | None = None
    session_id: str | None = None
    created_at: datetime


class QueryHistoryResponse(BaseModel):
    """Paginated query history."""

    items: list[QueryHistoryItem]
    total: int
    offset: int
    limit: int


class ConfidenceBucket(BaseModel):
    """Single confidence distribution bucket."""

    range: str
    count: int


class ConfidenceDistributionResponse(BaseModel):
    """Confidence score distribution histogram."""

    buckets: list[ConfidenceBucket]
