"""GET /api/v1/home: the business dashboard's first page (BIZ3)."""

import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import _load_scoped_corpus, get_session, resolve_scope
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.fair_use import FairUseTile  # PIP3 fair use
from callobuzz_rag.api.schemas.home import HomeOut
from callobuzz_rag.business.home import build_home, main_corpus
from callobuzz_rag.fairuse.views import home_tile  # PIP3 fair use

if TYPE_CHECKING:
    from callobuzz_rag.db.models.corpus import Corpus

router = APIRouter(prefix="/api/v1/home", tags=["home"])


@router.get("", response_model=HomeOut)
async def home(
    request: Request,
    corpus_id: uuid.UUID | None = Query(
        None,
        description="The knowledge base to summarise; omitted: the workspace's main one "
        "(its oldest).",
    ),
    session: AsyncSession = Depends(get_session),
) -> HomeOut:
    """Is the assistant live, what has it learned from the website, and how many people
    wrote today (in the business's own time zone). For a workspace sign-in or a `tk_` key."""
    scope = resolve_scope(request)
    if scope.corpus_id is not None:
        raise APIError(
            403, "Home belongs to a workspace, not to one knowledge base.", "scope_forbidden"
        )
    if scope.is_admin or scope.tenant_id is None:
        raise APIError(403, "There is no workspace here.", "no_workspace")
    corpus: Corpus | None
    if corpus_id is not None:
        corpus = await _load_scoped_corpus(request, corpus_id, session)
    else:
        corpus = await main_corpus(session, scope.tenant_id)
    out = await build_home(
        session,
        request.app.state.settings,
        corpus,
        tenant_id=scope.tenant_id,  # BIZ4 onboarding
    )
    # PIP3 fair use: a heads-up only while a limit is close or passed
    tile = await home_tile(session, request.app, scope.tenant_id)
    if tile is not None:
        out.fair_use = FairUseTile(
            state=tile[0], worst_metric=tile[1], pct=tile[2], resets_at=tile[3]
        )
    # /PIP3 fair use
    return out
