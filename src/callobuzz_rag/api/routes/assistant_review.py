"""Review inbox for one knowledge base (conversation C7)."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, path_corpus
from callobuzz_rag.api.schemas.assistant_quality import (
    ReviewActionOut,
    ReviewActionRequest,
    ReviewKind,
    ReviewList,
)
from callobuzz_rag.conversation.review import apply_review_action, list_review_items
from callobuzz_rag.db.models import Corpus

router = APIRouter(prefix="/api/v1/corpora/{corpus_id}/assistant/review", tags=["assistant-review"])

DEFAULT_WINDOW = timedelta(days=30)


@router.get("", response_model=ReviewList)
async def review_items(
    kind: ReviewKind | None = Query(None),
    since: datetime | None = Query(None, alias="from"),
    until: datetime | None = Query(None, alias="to"),
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> ReviewList:
    """Questions visitors could not get answered (no answer, shaky, thumbs down), grouped,
    most asked first. Items already acted on stay hidden until the problem comes back."""
    now = datetime.now(UTC)
    items = await list_review_items(
        session, corpus.id, kind=kind, since=since or now - DEFAULT_WINDOW, until=until or now
    )
    return ReviewList(items=items, count=len(items))


@router.post("/actions", response_model=ReviewActionOut, status_code=201)
async def review_action(
    body: ReviewActionRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> ReviewActionOut:
    """Act on an item: dismiss it, or add an exact answer / a rule to the assistant's draft."""
    return await apply_review_action(session, request, corpus, body)
