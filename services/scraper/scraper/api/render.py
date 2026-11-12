"""POST /api/v1/render: render one page in the headless browser for the engine (BIZ1).

The engine's website learning reads a business's own site. For a page that is an empty
JavaScript shell it asks this service to render it. The engine has no person's credential, so
this route is not behind the engine-key check: it has its own shared secret (`X-Render-Token`
against `SCRAPER_RENDER_TOKEN`, compared in constant time). With no token configured the route
does not exist (404). The URL still goes through the same SSRF guard as every other fetch, and
the browser's traffic is proxied through the guarded client exactly as in a crawl.
"""

import hmac

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from scraper.crawl.fetcher import PageFetcher
from scraper.crawl.netguard import BlockedURLError, check_url

router = APIRouter()


class RenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., min_length=1, max_length=2048)


class RenderResponse(BaseModel):
    url: str
    status_code: int
    html: str


@router.post("/api/v1/render", response_model=RenderResponse, include_in_schema=False)
async def render(
    body: RenderRequest,
    request: Request,
    x_render_token: str | None = Header(default=None),
) -> RenderResponse:
    settings = request.app.state.settings
    if not settings.render_token:
        raise HTTPException(status_code=404, detail="Not Found")
    if not x_render_token or not hmac.compare_digest(
        x_render_token.encode("utf-8"), settings.render_token.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid render token", headers={})
    try:
        await check_url(body.url, settings.allow_private_networks)
    except BlockedURLError as exc:
        raise HTTPException(
            status_code=422, detail={"detail": str(exc), "code": "blocked"}
        ) from exc

    fetcher = PageFetcher(
        playwright_timeout=settings.playwright_timeout,
        httpx_timeout=settings.httpx_timeout,
        allow_private_networks=settings.allow_private_networks,
        max_response_bytes=settings.max_response_bytes,
    )
    result = await fetcher._fetch_playwright(body.url)  # noqa: SLF001 - the browser path only
    if result.error:
        raise HTTPException(
            status_code=502, detail={"detail": "Render failed", "code": "render_failed"}
        )
    html = result.html.encode("utf-8")[: settings.max_response_bytes].decode("utf-8", "ignore")
    return RenderResponse(
        url=result.final_url or result.url, status_code=result.status_code, html=html
    )
