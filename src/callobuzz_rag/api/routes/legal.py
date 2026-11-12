"""`GET /legal/{slug}`: the published page as plain HTML, outside `/api` (P3).

A payment gateway's website check and an e-mail client both need a page that works without
JavaScript, so the engine renders it. Not in the OpenAPI spec, like `/privacy/withdraw`.
"""

from html import escape

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.settings_store import get_engine_settings
from callobuzz_rag.api.deps import get_session
from callobuzz_rag.legal import store
from callobuzz_rag.legal.pages import CSP, render_page
from callobuzz_rag.legal.slugs import SLUGS

router = APIRouter(tags=["legal"], include_in_schema=False)

_HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": CSP}


def _not_found(product_name: str) -> Response:
    name = escape(product_name)
    body = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Page not found · {name}</title></head>"
        '<body style="font:16px/1.6 system-ui,sans-serif;max-width:40rem;margin:48px auto;'
        'padding:0 16px"><h1>Page not found</h1>'
        f"<p>This page is not published on {name}.</p></body></html>"
    )
    return Response(
        body,
        status_code=404,
        media_type="text/html; charset=utf-8",
        headers={**_HEADERS, "Cache-Control": "no-store"},
    )


@router.get("/legal/{slug}")
async def legal_page(
    slug: str, request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    engine = await get_engine_settings(request.app.state.session_factory)
    branding = engine.branding
    page = await store.published(session, slug) if slug in SLUGS else None
    if page is None:
        return _not_found(branding.product_name)
    html = render_page(
        page,
        product_name=branding.product_name,
        accent=branding.primary_color,
        pages=await store.published_all(session),
    )
    return Response(
        html,
        media_type="text/html; charset=utf-8",
        headers={**_HEADERS, "Cache-Control": "public, max-age=60"},
    )
