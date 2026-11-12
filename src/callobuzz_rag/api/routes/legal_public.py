"""Published legal pages as JSON, for the dashboard, checkout and the website (P3).

No credential: `/api/v1/public/` is open. Only published pages are visible.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.routes.widget_keys import engine_public_url
from callobuzz_rag.api.schemas.legal import PublicLegalItem, PublicLegalList, PublicLegalPage
from callobuzz_rag.legal import service, store

router = APIRouter(prefix="/api/v1/public/legal", tags=["public"])


@router.get("", response_model=PublicLegalList)
async def list_public_pages(
    request: Request, session: AsyncSession = Depends(get_session)
) -> PublicLegalList:
    """The pages the deployer has published, with the address of each."""
    base = engine_public_url(request)
    return PublicLegalList(
        items=[
            PublicLegalItem(
                slug=p.slug,
                title=p.title,
                version=p.version,
                published_at=p.published_at,
                url=f"{base}/legal/{p.slug}",
            )
            for p in await store.published_all(session)
        ]
    )


@router.get("/{slug}", response_model=PublicLegalPage)
async def get_public_page(
    slug: str, session: AsyncSession = Depends(get_session)
) -> PublicLegalPage:
    """One published page: safe HTML and the Markdown it came from."""
    page = await store.published(session, service.check_slug(slug))
    if page is None:
        raise APIError(404, "That page is not published.", "legal_page_not_found")
    return PublicLegalPage(
        slug=page.slug,
        title=page.title,
        version=page.version,
        published_at=page.published_at,
        html=page.html,
        markdown=page.markdown,
    )
