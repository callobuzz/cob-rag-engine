"""Gap detection analytics endpoints."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import (
    Scope,
    authorized_corpus_ids,
    get_session,
    resolve_scope,
)
from callobuzz_rag.api.schemas.gaps import GapReportListResponse, GapReportResponse
from callobuzz_rag.db.models.gap_report import GapReport

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/analytics/gaps", tags=["analytics"])


@router.get("", response_model=GapReportListResponse)
async def list_gap_reports(
    offset: int = 0,
    limit: int = 20,
    corpus_id: uuid.UUID | None = Query(None, description="Filter by corpus ID"),
    session: AsyncSession = Depends(get_session),
    scope: Scope = Depends(resolve_scope),
) -> GapReportListResponse:
    """List gap detection reports with pagination."""
    # Callers have been sending corpus_id since before the column existed; FastAPI
    # silently discarded it, so every consumer got an unfiltered list back.
    allowed = await authorized_corpus_ids(scope, corpus_id, session)
    filters: list[ColumnElement[bool]] = []
    if allowed is not None:
        # .in_() excludes NULL, which is deliberate: a report with no corpus_id
        # spanned every corpus, so only an admin may see it.
        filters.append(GapReport.corpus_id.in_(allowed))
    elif corpus_id is not None:
        filters.append(GapReport.corpus_id == corpus_id)

    count_stmt = select(func.count()).select_from(GapReport).where(*filters)
    total = (await session.execute(count_stmt)).scalar_one()

    stmt = (
        select(GapReport)
        .where(*filters)
        .order_by(GapReport.started_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(stmt)
    reports = list(result.scalars().all())

    return GapReportListResponse(
        items=[GapReportResponse.model_validate(r) for r in reports],
        total=total,
    )


@router.get("/{report_id}", response_model=GapReportResponse)
async def get_gap_report(
    report_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    scope: Scope = Depends(resolve_scope),
) -> GapReportResponse:
    """Get a single gap detection report by ID."""
    report = await session.get(GapReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail=f"Gap report {report_id} not found")

    allowed = await authorized_corpus_ids(scope, report.corpus_id, session)
    if allowed is not None and report.corpus_id not in allowed:
        raise HTTPException(status_code=403, detail="Gap report is outside this key's scope.")

    return GapReportResponse.model_validate(report)


@router.post("/run", response_model=GapReportResponse, status_code=202)
async def run_gap_detection(
    request: Request,
    corpus_id: uuid.UUID | None = Query(None, description="Scope to a specific corpus"),
    session: AsyncSession = Depends(get_session),
    scope: Scope = Depends(resolve_scope),
) -> GapReportResponse:
    """Queue a gap detection run and return its report, status "queued".

    The worker embeds low-confidence queries and clusters them with BERTopic,
    which takes far longer than an HTTP request should stay open, so the run is
    a Celery task like every other background job. Poll
    GET /api/v1/analytics/gaps/{id} until the status is "completed" or "failed".
    """
    if corpus_id is None and scope.corpus_id is not None:
        # A corpus key's scope is exactly one corpus; that is the run it means.
        corpus_id = scope.corpus_id
    allowed = await authorized_corpus_ids(scope, corpus_id, session)
    if allowed is not None:
        if corpus_id is None:
            # A global run reads every tenant's queries; only admin may do that.
            raise HTTPException(
                status_code=400,
                detail="corpus_id is required unless authenticated as admin.",
            )
        if corpus_id not in allowed:
            raise HTTPException(status_code=403, detail="Corpus is outside this key's scope.")

    from callobuzz_rag.api.routes.admin import _dispatch_gap_detection_task

    report_id = await _dispatch_gap_detection_task(request, corpus_id)
    report = await session.get(GapReport, report_id)
    if report is None:  # pragma: no cover — committed a moment ago
        raise HTTPException(status_code=500, detail="Queued gap report could not be read back")
    return GapReportResponse.model_validate(report)
