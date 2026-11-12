"""Gap detection API request/response schemas."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class GapCluster(BaseModel):
    """A single topic cluster representing a knowledge gap."""

    topic_id: int
    keywords: list[str]
    query_count: int
    representative_queries: list[str]


class GapReportResponse(BaseModel):
    """Response body for a single gap detection report."""

    id: uuid.UUID
    # None means the run spanned every corpus, or predates the corpus_id column
    # and its corpus had already been deleted when phase8_002 backfilled.
    corpus_id: uuid.UUID | None = None
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    query_count: int
    cluster_count: int
    clusters: list[GapCluster] | None = None

    model_config = {"from_attributes": True}


class GapReportListResponse(BaseModel):
    """Paginated list of gap detection reports."""

    items: list[GapReportResponse]
    total: int
