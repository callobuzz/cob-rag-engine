"""Feedback endpoint — user feedback on query responses.

Supports explicit feedback (user-submitted ratings/corrections) and
exposes feedback statistics for analytics (Epic 3-08).
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import Scope, authorized_corpus_ids, get_session, resolve_scope
from callobuzz_rag.api.schemas.feedback import (
    FeedbackItem,
    FeedbackListResponse,
    FeedbackRequest,
    FeedbackResponse,
    FeedbackStatsResponse,
)
from callobuzz_rag.db.models.feedback import Feedback
from callobuzz_rag.db.models.query_log import QueryLog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackResponse, status_code=201)
async def submit_feedback(
    body: FeedbackRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> FeedbackResponse:
    """Submit feedback on a query response.

    Validates that the referenced query exists, then persists the feedback
    record. Accepts optional session_id and trace_id for session tracking
    and Langfuse observability linking.
    """
    query_log = await session.get(QueryLog, body.query_id)
    if not query_log:
        raise HTTPException(
            status_code=404,
            detail=f"Query {body.query_id} not found",
        )

    # Scoping: a corpus key may only rate its own queries, a tenant key its
    # corpora's; admin may rate any.
    allowed = await authorized_corpus_ids(resolve_scope(request), None, session)
    if allowed is not None and query_log.corpus_id not in allowed:
        raise HTTPException(
            status_code=403,
            detail="Query does not belong to this corpus",
        )

    feedback = Feedback(
        query_log_id=body.query_id,
        rating=body.rating,
        correction=body.correction,
        feedback_type="explicit",
        session_id=body.session_id,
        trace_id=body.trace_id,
    )
    session.add(feedback)
    await session.commit()
    await session.refresh(feedback)

    logger.info(
        "Feedback recorded: query_id=%s rating=%d session=%s",
        body.query_id,
        body.rating,
        body.session_id,
    )

    return FeedbackResponse(feedback_id=feedback.id)


@router.get("", response_model=FeedbackListResponse)
async def list_feedback(
    corpus_id: UUID | None = Query(None, description="Only feedback on this corpus's answers."),
    min_rating: int | None = Query(None, ge=1, le=5),
    max_rating: int | None = Query(None, ge=1, le=5),
    start: datetime | None = Query(None, description="Given at or after (ISO 8601)."),
    end: datetime | None = Query(None, description="Given before (ISO 8601)."),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    scope: Scope = Depends(resolve_scope),
) -> FeedbackListResponse:
    """Ratings, newest first, each with its question and query id (for golden from-feedback).

    Scoped through the rated query's corpus: admin sees all, a tenant its
    corpora, a corpus key its own.
    """
    if min_rating is not None and max_rating is not None and min_rating > max_rating:
        raise HTTPException(status_code=422, detail="min_rating is above max_rating")
    allowed = await authorized_corpus_ids(scope, corpus_id, session)

    where: list[ColumnElement[bool]] = []
    if allowed is not None:
        where.append(QueryLog.corpus_id.in_(allowed))
    if min_rating is not None:
        where.append(Feedback.rating >= min_rating)
    if max_rating is not None:
        where.append(Feedback.rating <= max_rating)
    if start is not None:
        where.append(Feedback.created_at >= start)
    if end is not None:
        where.append(Feedback.created_at < end)

    joined = select(Feedback).outerjoin(QueryLog, QueryLog.id == Feedback.query_log_id)
    total = (
        await session.execute(
            select(func.count(Feedback.id))
            .select_from(Feedback)
            .outerjoin(QueryLog, QueryLog.id == Feedback.query_log_id)
            .where(*where)
        )
    ).scalar() or 0
    rows = (
        await session.execute(
            joined.add_columns(QueryLog.query_text, QueryLog.corpus_id)
            .where(*where)
            .order_by(Feedback.created_at.desc(), Feedback.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return FeedbackListResponse(
        items=[
            FeedbackItem(
                id=fb.id,
                query_id=fb.query_log_id,
                corpus_id=fb_corpus,
                question=question,
                rating=fb.rating,
                correction=fb.correction,
                feedback_type=fb.feedback_type,
                session_id=fb.session_id,
                created_at=fb.created_at,
            )
            for fb, question, fb_corpus in rows
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/stats", response_model=FeedbackStatsResponse)
async def get_feedback_stats(
    corpus_id: UUID = Query(..., description="Corpus ID to get stats for"),
    start: datetime | None = Query(
        None, description="Start of time range (ISO 8601). Defaults to 30 days ago."
    ),
    end: datetime | None = Query(
        None, description="End of time range (ISO 8601). Defaults to now."
    ),
    session: AsyncSession = Depends(get_session),
    scope: Scope = Depends(resolve_scope),
) -> FeedbackStatsResponse:
    """Get aggregated feedback statistics for a corpus."""
    allowed = await authorized_corpus_ids(scope, corpus_id, session)
    if allowed is not None and corpus_id not in allowed:
        raise HTTPException(status_code=403, detail="Corpus is outside this key's scope.")

    from callobuzz_rag.analytics.feedback_enrichment import FeedbackEnrichmentService

    now = datetime.now(UTC)
    effective_start = start or (now - timedelta(days=30))
    effective_end = end or now

    service = FeedbackEnrichmentService(session)
    stats = await service.get_feedback_stats(corpus_id, effective_start, effective_end)

    return FeedbackStatsResponse(
        corpus_id=stats.corpus_id,
        total=stats.total,
        positive=stats.positive,
        negative=stats.negative,
        corrections=stats.corrections,
        implicit=stats.implicit,
        start=effective_start,
        end=effective_end,
    )
