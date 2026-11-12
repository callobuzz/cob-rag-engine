"""Articles and FAQ entries written in the dashboard (BIZ3).

The owner's text is kept in `knowledge_articles` (so it can be edited later) and learned as
a records import, `article:<id>` / `faq:<id>`, through the same add-path as
`POST /ingest/records`: one plan-limit check, one job, one task. Deleting an article is
`DELETE /documents/{id}`.
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.caps import check_document_room
from callobuzz_rag.api.deps import get_session, path_corpus
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.articles import ArticleOut, ArticleRequest
from callobuzz_rag.cob.system import refuse_system_write
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.knowledge_article import KnowledgeArticle
from callobuzz_rag.pipeline.records import TextRecord
from callobuzz_rag.pipeline.records_queue import queue_records

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/corpora/{corpus_id}/articles", tags=["knowledge"])


def _not_found() -> APIError:
    return APIError(404, "That article was not found in this knowledge base.", "article_not_found")


def _record(row: KnowledgeArticle) -> TextRecord:
    """What the import learns: headings in the body become headings of the learned text."""
    return TextRecord(
        id=row.external_id,
        title=row.title,
        text=row.body,
        markdown=True,
        metadata={"source": "articles", "kind": row.kind},
    )


def _author(request: Request) -> str:
    user = getattr(request.state, "user", None)
    email = getattr(user, "email", None)
    return str(email)[:320] if email else "api"


async def _document_id(
    session: AsyncSession, corpus_id: uuid.UUID, external_id: str
) -> uuid.UUID | None:
    found = await session.execute(
        select(Document.id).where(
            Document.corpus_id == corpus_id, Document.external_id == external_id
        )
    )
    return found.scalars().first()


async def _find(session: AsyncSession, corpus: Corpus, ident: uuid.UUID) -> KnowledgeArticle:
    """The article by its own id or by the id of the document it became; this knowledge
    base's only."""
    row = await session.get(KnowledgeArticle, ident)
    if row is not None and row.corpus_id == corpus.id:
        return row
    doc = await session.get(Document, ident)
    if doc is None or doc.corpus_id != corpus.id or not doc.external_id:
        raise _not_found()
    found = await session.execute(
        select(KnowledgeArticle).where(
            KnowledgeArticle.corpus_id == corpus.id,
            KnowledgeArticle.external_id == doc.external_id,
        )
    )
    row = found.scalars().first()
    if row is None:
        raise _not_found()
    return row


def _out(
    row: KnowledgeArticle,
    document_id: uuid.UUID | None,
    *,
    job_id: uuid.UUID | None = None,
) -> ArticleOut:
    return ArticleOut(
        id=row.id,
        document_id=document_id,
        kind=row.kind,
        title=row.title,
        body=row.body,
        status="ready" if document_id is not None else "queued",
        job_id=job_id,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _discard(session: AsyncSession, row: KnowledgeArticle) -> None:
    """The import was refused: no article row stays behind (before or after its commit)."""
    if row in session.new:
        session.expunge(row)
        return
    await session.rollback()
    await session.delete(row)
    await session.commit()


@router.post("", response_model=ArticleOut, status_code=202)
async def create_article(
    body: ArticleRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(path_corpus),
) -> ArticleOut:
    """Write an article or an FAQ answer; the assistant learns it in a moment.

    Counts as one document of the plan (409 `document_limit_reached` when the plan is
    full). Workspace members may write too.
    """
    refuse_system_write(request, corpus)
    article_id = uuid.uuid4()
    row = KnowledgeArticle(
        id=article_id,
        corpus_id=corpus.id,
        tenant_id=corpus.tenant_id,
        external_id=f"{body.kind}:{article_id}",
        kind=body.kind,
        title=body.title,
        body=body.body,
        created_by=_author(request),
    )
    session.add(row)  # committed with the import job, or not at all
    try:
        job = await queue_records(
            session, corpus, [_record(row)], mode="records", room_check=check_document_room
        )
    except HTTPException:
        await _discard(session, row)
        raise
    await session.refresh(row)
    return _out(row, None, job_id=job.id)


@router.get("/{document_id}", response_model=ArticleOut)
async def get_article(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(path_corpus),
) -> ArticleOut:
    """An article or FAQ entry with the text as written. `document_id` is the knowledge
    base document (as listed by `GET /documents?source=articles`) or the article's own id."""
    row = await _find(session, corpus, document_id)
    return _out(row, await _document_id(session, corpus.id, row.external_id))


@router.put("/{document_id}", response_model=ArticleOut, status_code=202)
async def update_article(
    document_id: uuid.UUID,
    body: ArticleRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(path_corpus),
) -> ArticleOut:
    """Change the text. The assistant learns the new version (the old one stays in the
    document's history). The kind cannot change."""
    refuse_system_write(request, corpus)
    row = await _find(session, corpus, document_id)
    if body.kind != row.kind:
        raise APIError(
            422,
            "An article cannot become a question and answer, or the other way round. "
            "Write a new one instead.",
            "article_kind_changed",
        )
    row.title = body.title
    row.body = body.body
    row.updated_at = datetime.now(UTC)
    try:
        job = await queue_records(
            session, corpus, [_record(row)], mode="records", room_check=check_document_room
        )
    except HTTPException:
        await session.rollback()
        raise
    await session.refresh(row)
    return _out(row, await _document_id(session, corpus.id, row.external_id), job_id=job.id)
