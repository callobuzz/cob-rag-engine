"""Browser reading through the scraper (BIZ1): `POST {RAG_WEBSITE_RENDER_URL}/api/v1/render`.

Only for pages that are an empty JavaScript shell when fetched plainly. The scraper (its own
service, with a guarded Playwright path) renders the page and answers `{url, status_code, html}`;
the engine authenticates with the shared render token, never with anyone's key. The address is the
deployer's own configuration (an internal service name in compose), so this is a plain client,
not the SSRF-guarded one: the page URL itself was vetted before it is sent.
"""

import logging
from dataclasses import dataclass

import httpx

from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

RENDER_TIMEOUT_SECONDS = 90.0


@dataclass(frozen=True)
class RenderResult:
    url: str  # the final URL the browser ended on
    status: int
    html: str


def render_configured(settings: Settings) -> bool:
    return bool(
        settings.website_render_url.strip() and settings.website_render_token.get_secret_value()
    )


async def render_page(
    settings: Settings, url: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> RenderResult | None:
    """The page as a browser sees it, or None when the scraper cannot or will not render it."""
    if not render_configured(settings):
        return None
    endpoint = settings.website_render_url.rstrip("/") + "/api/v1/render"
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=RENDER_TIMEOUT_SECONDS, trust_env=False
        ) as client:
            response = await client.post(
                endpoint,
                json={"url": url},
                headers={"X-Render-Token": settings.website_render_token.get_secret_value()},
            )
        if response.status_code != 200:
            logger.info("render refused with HTTP %s", response.status_code)
            return None
        data = response.json()
        return RenderResult(
            url=str(data["url"]), status=int(data["status_code"]), html=str(data["html"])
        )
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        logger.info("render failed", exc_info=True)
        return None
