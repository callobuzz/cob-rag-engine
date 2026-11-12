"""The in-app Docs section: the help pages, filtered to what the signed-in role may read.

Session-only (people, not keys). Pages come straight from the files shipped with the engine,
the same sources COB's knowledge base is built from. Search is free: it never counts as a COB
question and never calls a model.
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, require_session_user
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.docs import DocPageOut, DocsSearchOut, DocsTree
from callobuzz_rag.cob import docs_service
from callobuzz_rag.cob.kb import load_docs_index
from callobuzz_rag.cob.orchestrator import caller_from_request
from callobuzz_rag.cob.types import allowed_audiences

router = APIRouter(
    prefix="/api/v1/docs", tags=["docs"], dependencies=[Depends(require_session_user)]
)


def _audiences(request: Request) -> list[str]:
    return allowed_audiences(caller_from_request(request).role)


@router.get("/tree", response_model=DocsTree, summary="The Docs table of contents")
async def docs_tree(request: Request, session: AsyncSession = Depends(get_session)) -> DocsTree:
    """Topics and pages your role may read, with the docs version. Pages your platform added
    come last, under "From your platform"."""
    audiences = _audiences(request)
    tree = docs_service.tree_for(load_docs_index(), audiences)
    extra = await docs_service.platform_topic(session, audiences)  # COB9
    if extra is not None:
        tree.topics.append(extra)
    return tree


@router.get("/search", response_model=DocsSearchOut, summary="Search the Docs")
async def docs_search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(10, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
) -> DocsSearchOut:
    """Pages that match, best first. Free: never counts as a COB question and uses no model."""
    q = q.strip()
    if not q:
        raise APIError(422, "Type something to search for.", "docs_query_empty")
    return await docs_service.search(
        request, session, q, _audiences(request), limit=limit, index=load_docs_index()
    )


@router.get("/pages/{slug:path}", response_model=DocPageOut, summary="Read a Docs page")
async def docs_page(
    slug: str, request: Request, session: AsyncSession = Depends(get_session)
) -> DocPageOut:
    """One page as Markdown with its contents list. A page you may not read is a 404."""
    index = load_docs_index()
    if slug.startswith(docs_service.PLATFORM_SLUG_PREFIX):  # COB9
        return await docs_service.platform_page(session, slug, _audiences(request), index.version)
    return docs_service.page_for(index, slug, _audiences(request))
