"""Outcome analytics: one knowledge base, or every knowledge base in the caller's scope (C7)."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import authorized_corpus_ids, get_session, path_corpus, resolve_scope
from callobuzz_rag.api.schemas.assistant_quality import OutcomesResponse
from callobuzz_rag.conversation.analytics import build_outcomes, fetch_outcome_rows
from callobuzz_rag.db.models import Corpus

corpus_router = APIRouter(
    prefix="/api/v1/corpora/{corpus_id}/assistant/analytics", tags=["assistant-analytics"]
)
scope_router = APIRouter(prefix="/api/v1/assistant/analytics", tags=["assistant-analytics"])


def _period(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    # Whole UTC days: usage is stored per day, so a start inside a day would count that whole
    # day's spend against fewer conversations. A client that wants every day sends an early `from`.
    since = start or now - timedelta(days=30)
    since = since.replace(tzinfo=UTC) if since.tzinfo is None else since.astimezone(UTC)
    return since.replace(hour=0, minute=0, second=0, microsecond=0), end or now


@corpus_router.get("", response_model=OutcomesResponse)
async def corpus_outcomes(
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> OutcomesResponse:
    since, until = _period(start, end)
    return build_outcomes(
        await fetch_outcome_rows(session, [corpus.id], since, until), since, until
    )


@scope_router.get("", response_model=OutcomesResponse)
async def scope_outcomes(
    request: Request,
    corpus_id: uuid.UUID | None = Query(None),
    start: datetime | None = Query(None, alias="from"),
    end: datetime | None = Query(None, alias="to"),
    session: AsyncSession = Depends(get_session),
) -> OutcomesResponse:
    since, until = _period(start, end)
    allowed = await authorized_corpus_ids(resolve_scope(request), corpus_id, session)
    ids = (
        list(allowed)
        if allowed is not None
        else list(
            (await session.execute(select(Corpus.id).where(Corpus.is_system.is_(False))))
            .scalars()
            .all()
        )
    )
    return build_outcomes(await fetch_outcome_rows(session, ids, since, until), since, until)
