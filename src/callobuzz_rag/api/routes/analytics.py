"""Analytics endpoints — query counts, trends, history, and confidence distribution."""

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.analytics.stats_service import QueryStatsService, TrendGranularity
from callobuzz_rag.api.deps import (
    Scope,
    authorized_corpus_ids,
    get_session,
    resolve_scope,
)
from callobuzz_rag.api.schemas.analytics import (
    ConfidenceBucket,
    ConfidenceDistributionResponse,
    QueryCountResponse,
    QueryHistoryItem,
    QueryHistoryResponse,
    TrendPoint,
    TrendsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.get("/queries/count", response_model=QueryCountResponse)
async def query_count(
    session: AsyncSession = Depends(get_session),
    corpus_id: uuid.UUID | None = Query(None, description="Filter by corpus ID"),
    start: datetime | None = Query(None, description="Start of date range (inclusive)"),
    end: datetime | None = Query(None, description="End of date range (inclusive)"),
    scope: Scope = Depends(resolve_scope),
) -> QueryCountResponse:
    """Return the total number of queries, with optional filters."""
    allowed = await authorized_corpus_ids(scope, corpus_id, session)
    svc = QueryStatsService()
    count = await svc.get_query_count(session, corpus_ids=allowed, start=start, end=end)
    return QueryCountResponse(count=count)


@router.get("/queries/trends", response_model=TrendsResponse)
async def query_trends(
    session: AsyncSession = Depends(get_session),
    corpus_id: uuid.UUID | None = Query(None, description="Filter by corpus ID"),
    start: datetime | None = Query(None, description="Start of date range (inclusive)"),
    end: datetime | None = Query(None, description="End of date range (inclusive)"),
    granularity: TrendGranularity = Query(
        "day", description="Aggregation granularity: hour, day, week, month"
    ),
    scope: Scope = Depends(resolve_scope),
) -> TrendsResponse:
    """Return time-series trend data (count, avg confidence, avg latency)."""
    allowed = await authorized_corpus_ids(scope, corpus_id, session)
    svc = QueryStatsService()
    rows = await svc.get_trends(
        session, corpus_ids=allowed, start=start, end=end, granularity=granularity
    )
    return TrendsResponse(
        data=[
            TrendPoint(
                bucket=row["bucket"],
                count=row["count"],
                avg_confidence=row["avg_confidence"],
                avg_latency=row["avg_latency"],
            )
            for row in rows
        ]
    )


@router.get("/queries/history", response_model=QueryHistoryResponse)
async def query_history(
    session: AsyncSession = Depends(get_session),
    corpus_id: uuid.UUID | None = Query(None, description="Filter by corpus ID"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(20, ge=1, le=100, description="Page size"),
    session_id: str | None = Query(None, description="Filter by session ID"),
    scope: Scope = Depends(resolve_scope),
) -> QueryHistoryResponse:
    """Return paginated query history."""
    allowed = await authorized_corpus_ids(scope, corpus_id, session)
    svc = QueryStatsService()
    items, total = await svc.get_query_history(
        session, corpus_ids=allowed, offset=offset, limit=limit, session_id=session_id
    )
    return QueryHistoryResponse(
        items=[QueryHistoryItem.model_validate(item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/queries/confidence", response_model=ConfidenceDistributionResponse)
async def confidence_distribution(
    session: AsyncSession = Depends(get_session),
    corpus_id: uuid.UUID | None = Query(None, description="Filter by corpus ID"),
    start: datetime | None = Query(None, description="Start of date range (inclusive)"),
    end: datetime | None = Query(None, description="End of date range (inclusive)"),
    scope: Scope = Depends(resolve_scope),
) -> ConfidenceDistributionResponse:
    """Return confidence score distribution histogram."""
    allowed = await authorized_corpus_ids(scope, corpus_id, session)
    svc = QueryStatsService()
    dist = await svc.get_confidence_distribution(session, corpus_ids=allowed, start=start, end=end)
    return ConfidenceDistributionResponse(
        buckets=[ConfidenceBucket(range=b["range"], count=b["count"]) for b in dist]
    )
