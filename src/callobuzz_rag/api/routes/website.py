"""Website learning, as the business owner sees it (BIZ1).

The engine reads the owner's own site (only sites listed as allowed sites of a widget key),
politely and under the deployer's page limit; these routes show what it learned and why a page
was or was not learned, and let the owner switch it off, leave pages out and read again.
`website/service.py` does the work; the learning itself runs in the worker.
"""

import uuid
from typing import Annotated, Literal  # BIZ4 suggestions: Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, path_corpus, require_owner
from callobuzz_rag.api.schemas.onboarding import SuggestionOut, SuggestionsOut  # BIZ4 suggestions
from callobuzz_rag.api.schemas.website import (
    LearnKeyOut,  # BIZ2 freshness
    LearnKeyStateOut,  # BIZ2 freshness
    LearnRequest,
    PageOut,
    PagesOut,
    RereadOut,
    SourceOut,
    UpdatePageRequest,
    UpdateWebsiteRequest,
    WebsiteOut,
    WebsitePlanOut,
    WebsiteSettingsOut,
)
from callobuzz_rag.cob.system import refuse_system_write
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.website import WebsitePage, WebsiteSource
from callobuzz_rag.website import push, service  # BIZ2 freshness: push

router = APIRouter(prefix="/api/v1/corpora/{corpus_id}/website", tags=["website"])


def _settings(request: Request):  # type: ignore[no-untyped-def]
    return request.app.state.settings


def _vector_store(request: Request):  # type: ignore[no-untyped-def]
    return request.app.state.vector_store


async def _corpus(session: AsyncSession, scoped: Corpus) -> Corpus:
    """The scoped corpus as a row of this session (a corpus key's copy is detached)."""
    row = await session.get(Corpus, scoped.id)
    if row is None:
        raise HTTPException(status_code=404, detail="Corpus not found")
    return row


def _source_out(source: WebsiteSource) -> SourceOut:
    return SourceOut(
        id=source.id,
        origin=source.origin,
        scope=source.scope,
        status=source.status,
        pause_reason=source.pause_reason,
        pages_found=source.pages_found,
        pages_learned=source.pages_learned,
        pages_skipped=source.pages_skipped,
        first_pass_done_at=source.first_pass_done_at,
        last_run_started_at=source.last_run_started_at,
        last_run_finished_at=source.last_run_finished_at,
        last_error_code=source.last_error_code,
    )


def _page_out(page: WebsitePage) -> PageOut:
    return PageOut(
        id=page.id,
        source_id=page.source_id,
        url=page.url,
        title=page.title,
        status=page.status,
        skip_reason=page.skip_reason,
        duplicate_of=page.duplicate_of,
        http_status=page.http_status,
        depth=page.depth,
        words=page.words,
        in_sitemap=page.in_sitemap,
        excluded_by_owner=page.excluded_by_owner,
        document_id=page.document_id,
        etag=page.etag,
        last_modified=page.last_modified,
        content_hash=page.content_hash,
        last_seen_at=page.last_seen_at,
        last_checked_at=page.last_checked_at,
        last_changed_at=page.last_changed_at,
        check_count=page.check_count,
        change_count=page.change_count,
    )


async def _overview(request: Request, session: AsyncSession, corpus: Corpus) -> WebsiteOut:
    settings = _settings(request)
    row = await service.get_settings_row(session, corpus)
    sources = await service.sources_of(session, corpus.id)
    held = {s.scope for s in sources}
    allowed = await service.allowed_sites(session, corpus.id)
    policy = await service.policy_of(session, corpus, settings)
    workspace = (
        (
            await session.execute(
                select(WebsiteSource).where(WebsiteSource.tenant_id == corpus.tenant_id)
            )
        )
        .scalars()
        .all()
    )
    learned = sum(s.pages_learned for s in workspace)
    return WebsiteOut(
        settings=WebsiteSettingsOut(
            enabled=row.enabled if row is not None else True,
            exclude_patterns=list(row.exclude_patterns) if row is not None else [],
            last_reread_at=row.last_reread_at if row is not None else None,
            refresh_mode=row.refresh_mode if row is not None else "daily",  # BIZ2 freshness
            adaptive=row.adaptive if row is not None else True,
        ),
        plan=WebsitePlanOut(
            learning_enabled=policy.reason in (None, "kb"),
            pages_cap=policy.base_cap,
            pages_learned=learned,
            reason=policy.reason,
        ),
        sources=[_source_out(s) for s in sources],
        learn_key=LearnKeyStateOut(  # BIZ2 freshness
            set=row is not None and row.learn_key_hash is not None,
            hint=row.learn_key_hint if row is not None else None,
            created_at=row.learn_key_created_at if row is not None else None,
        ),
        unlearned_origins=[origin for scope, origin in allowed.items() if scope not in held],
    )


@router.get("", response_model=WebsiteOut)
async def get_website(
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> WebsiteOut:
    """The knowledge base's website learning: settings, the sites and how far each is."""
    return await _overview(request, session, await _corpus(session, corpus))


@router.patch("", response_model=WebsiteOut)
async def update_website(
    body: UpdateWebsiteRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> WebsiteOut:
    """Switch learning on or off and choose paths to leave out (`/blog/*`, `/careers`)."""
    row = await _corpus(session, corpus)
    refuse_system_write(request, row)
    await service.update_settings(
        session,
        row,
        enabled=body.enabled,
        exclude_patterns=body.exclude_patterns,
        vector_store=_vector_store(request),
        request=request,
        refresh_mode=body.refresh_mode,  # BIZ2 freshness
        adaptive=body.adaptive,
    )
    return await _overview(request, session, row)


@router.post("/learn", response_model=SourceOut, status_code=202)
async def learn_website(
    body: LearnRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> SourceOut:
    """Start reading one of this knowledge base's allowed sites."""
    row = await _corpus(session, corpus)
    refuse_system_write(request, row)
    source = await service.start_learning(
        session, row, body.origin, settings=_settings(request), request=request
    )
    return _source_out(source)


@router.post("/reread", response_model=RereadOut, status_code=202)
async def reread_website(
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> RereadOut:
    """Read every site again, from the start. Once every 10 minutes."""
    row = await _corpus(session, corpus)
    refuse_system_write(request, row)
    queued = await service.reread_all(session, row, settings=_settings(request), request=request)
    return RereadOut(queued=queued)


@router.delete("/sources/{source_id}", status_code=204)
async def remove_website_source(
    source_id: uuid.UUID,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> None:
    """Stop learning a site: its pages leave the answers (they keep their history)."""
    row = await _corpus(session, corpus)
    refuse_system_write(request, row)
    source = await service.get_source(session, row.id, source_id)
    await service.remove_source(
        session, row, source, vector_store=_vector_store(request), request=request
    )


@router.get("/pages", response_model=PagesOut)
async def website_pages(
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
    status: Annotated[str | None, Query(max_length=12)] = None,
    source_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> PagesOut:
    """Every page found on the site, and what became of it."""
    rows, total, counts, reasons = await service.list_pages(
        session, corpus.id, status=status, source_id=source_id, q=q, offset=offset, limit=limit
    )
    return PagesOut(items=[_page_out(p) for p in rows], total=total, counts=counts, reasons=reasons)


@router.post("/pages/{page_id}/reread", response_model=PageOut, status_code=202)
async def reread_website_page(
    page_id: uuid.UUID,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> PageOut:
    """Read one page again now."""
    row = await _corpus(session, corpus)
    refuse_system_write(request, row)
    page = await service.get_page(session, row.id, page_id)
    if not await service.reread_page(
        session, row, page, settings=_settings(request), request=request
    ):
        raise HTTPException(status_code=503, detail=service.QUEUE_DOWN)
    return _page_out(page)


@router.patch("/pages/{page_id}", response_model=PageOut)
async def update_website_page(
    page_id: uuid.UUID,
    body: UpdatePageRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> PageOut:
    """Leave a page out of the answers (`excluded: true`) or put it back."""
    row = await _corpus(session, corpus)
    refuse_system_write(request, row)
    page = await service.get_page(session, row.id, page_id)
    await service.set_page_excluded(
        session,
        row,
        page,
        body.excluded,
        vector_store=_vector_store(request),
        settings=_settings(request),
        request=request,
    )
    return _page_out(page)


# BIZ2 freshness
@router.post("/learn-key", response_model=LearnKeyOut, status_code=201)
async def create_learn_key(
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> LearnKeyOut:
    """Make the push link your website can call when you publish a page. Shown once; making a
    new one replaces (and stops) the old one."""
    from callobuzz_rag.api.routes.widget_keys import engine_public_url

    row = await _corpus(session, corpus)
    refuse_system_write(request, row)
    # A link that every push would answer 409 for is not worth making.
    service.refuse_disabled(await service.policy_of(session, row, _settings(request)))
    raw = await push.create_key(session, row, request)
    return LearnKeyOut(
        key=raw,
        push_url=f"{engine_public_url(request)}/api/v1/public/learn/{raw}",
        hint=raw[-4:],
    )


@router.delete("/learn-key", status_code=204)
async def delete_learn_key(
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> None:
    """Remove the push link: it stops working at once."""
    row = await _corpus(session, corpus)
    refuse_system_write(request, row)
    await push.revoke_key(session, row, request)


# /BIZ2 freshness


# BIZ4 suggestions
@router.get("/suggestions", response_model=SuggestionsOut)
async def website_suggestions(
    lang: Literal["en", "hi-Latn"] = Query("en", description="en or hi-Latn (Hinglish)"),
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> SuggestionsOut:
    """Up to six first questions to try, made from the pages the assistant learned (and the
    opening hours of the business card). No model is called."""
    from callobuzz_rag.onboarding.service import suggestions_for

    return SuggestionsOut(
        items=[SuggestionOut(text=t) for t in await suggestions_for(session, corpus.id, lang)]
    )


# /BIZ4 suggestions
