"""scraper/crawl/fetcher.py — Hybrid page fetcher (httpx + Playwright fallback).

Neither path lets anything connect on its own. httpx goes through the guarded
client (``scraper.crawl.netguard``). The headless browser never touches the
network: every http(s) request it makes is intercepted and performed by the same
guarded client, then handed back with ``route.fulfill``; data:/blob: URLs stay
in-page; everything else, WebSockets and service workers included, is refused.
Both paths share the same per-response size limit and time budget.
"""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from scraper.crawl.netguard import (
    DEFAULT_MAX_RESPONSE_BYTES,
    BlockedURLError,
    ResponseTooLargeError,
    check_url,
    guarded_get,
    guarded_request,
    make_client,
)

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Request headers the browser sets that must not be forwarded as-is: framing is
# redone by httpx, and httpx should negotiate only encodings it can decode.
_STRIP_REQUEST_HEADERS = frozenset(
    {"host", "content-length", "connection", "keep-alive", "transfer-encoding",
     "accept-encoding", "upgrade", "proxy-connection"}
)

RouteHandler = Callable[[Any], Awaitable[None]]


@dataclass
class FetchResult:
    """Result of fetching a single URL."""

    url: str
    html: str
    method: str  # "httpx" or "playwright"
    status_code: int
    error: str | None = None
    # Where the browser ended up (a script may navigate away); only set by the Playwright path.
    final_url: str | None = None


# Patterns that indicate a JavaScript shell with no server-rendered content.
_JS_SHELL_PATTERNS = [
    re.compile(r'<div\s+id=["\']app["\']\s*>\s*</div>', re.IGNORECASE),
    re.compile(r'<div\s+id=["\']root["\']\s*>\s*</div>', re.IGNORECASE),
    re.compile(r'<div\s+id=["\']__next["\']\s*>\s*</div>', re.IGNORECASE),
    re.compile(r'<div\s+id=["\']__nuxt["\']\s*>\s*</div>', re.IGNORECASE),
]

_MIN_CONTENT_LENGTH = 500


class PageFetcher:
    """Fetch pages with httpx first, falling back to Playwright for JS-heavy sites."""

    def __init__(
        self,
        httpx_timeout: int = 30,
        playwright_timeout: int = 60,
        allow_private_networks: bool = False,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self.httpx_timeout = httpx_timeout
        self.playwright_timeout = playwright_timeout
        self.allow_private = allow_private_networks
        self.max_response_bytes = max_response_bytes

    async def fetch(self, url: str) -> FetchResult:
        """Fetch a URL — try httpx first, fall back to Playwright if needed.

        Every URL, redirect hop and browser request is checked by
        ``scraper.crawl.netguard`` first; a blocked one is reported as an error.
        """
        try:
            await check_url(url, self.allow_private)
        except BlockedURLError as exc:
            return FetchResult(url=url, html="", method="httpx", status_code=0,
                               error=f"Blocked: {exc}")
        result = await self._fetch_httpx(url)

        if result.error:
            return result

        # If content looks like a JS shell or is too thin, retry with Playwright
        if (
            len(result.html) < _MIN_CONTENT_LENGTH
            or self._looks_like_js_shell(result.html)
        ):
            pw_result = await self._fetch_playwright(url)
            if not pw_result.error:
                return pw_result

        return result

    async def _fetch_httpx(self, url: str) -> FetchResult:
        """Fetch a URL using httpx with checked redirects and a browser-like User-Agent."""
        try:
            async with make_client(self.allow_private, self.httpx_timeout) as client:
                response = await guarded_get(
                    client,
                    url,
                    self.allow_private,
                    headers={"User-Agent": USER_AGENT},
                    max_bytes=self.max_response_bytes,
                    total_timeout=self.httpx_timeout,
                )
                if response.status_code >= 400:
                    return FetchResult(
                        url=url,
                        html="",
                        method="httpx",
                        status_code=response.status_code,
                        error=f"HTTP {response.status_code}",
                    )
                return FetchResult(
                    url=url,
                    html=response.text,
                    method="httpx",
                    status_code=response.status_code,
                )
        except BlockedURLError as exc:
            return FetchResult(
                url=url, html="", method="httpx", status_code=0, error=f"Blocked: {exc}"
            )
        except ResponseTooLargeError as exc:
            return FetchResult(url=url, html="", method="httpx", status_code=0, error=str(exc))
        except httpx.TimeoutException:
            return FetchResult(
                url=url,
                html="",
                method="httpx",
                status_code=0,
                error="Timeout",
            )
        except httpx.HTTPError as exc:
            return FetchResult(
                url=url,
                html="",
                method="httpx",
                status_code=0,
                error=str(exc),
            )

    async def _fetch_playwright(self, url: str) -> FetchResult:
        """Render a URL in headless Chromium, with all its traffic proxied by us."""
        try:
            from playwright.async_api import async_playwright

            async with (
                async_playwright() as p,
                make_client(self.allow_private, self.httpx_timeout) as client,
            ):
                browser = await p.chromium.launch(headless=True)
                try:
                    # Service workers could fetch outside page.route(); block them.
                    context = await browser.new_context(
                        service_workers="block", user_agent=USER_AGENT
                    )
                    await context.route("**/*", self._make_route_handler(client))
                    await context.route_web_socket(re.compile(".*"), self._refuse_websocket)
                    page = await context.new_page()
                    response = await page.goto(
                        url,
                        wait_until="networkidle",
                        timeout=self.playwright_timeout * 1000,
                    )
                    html = await page.content()
                    final_url = page.url
                    status_code = response.status if response else 200
                finally:
                    await browser.close()
                return FetchResult(
                    url=url,
                    html=html,
                    method="playwright",
                    status_code=status_code,
                    final_url=final_url,
                )
        except Exception as exc:  # noqa: BLE001
            return FetchResult(
                url=url,
                html="",
                method="playwright",
                status_code=0,
                error=str(exc),
            )

    def _make_route_handler(self, client: httpx.AsyncClient) -> RouteHandler:
        """A Playwright route handler that performs each request itself.

        The browser never connects anywhere: http(s) requests are sent by the
        guarded client (address checks at check time and at connect time, manual
        redirects, size cap, time budget) and the result is fulfilled back.
        """

        async def handle(route: Any) -> None:
            request = route.request
            url: str = request.url
            if url.startswith(("data:", "blob:")):
                await route.continue_()  # in-page data, no network access
                return
            if not url.lower().startswith(("http://", "https://")):
                await route.abort("blockedbyclient")
                return
            headers = {
                k: v
                for k, v in dict(request.headers).items()
                if k.lower() not in _STRIP_REQUEST_HEADERS
            }
            try:
                response = await guarded_request(
                    client,
                    request.method,
                    url,
                    self.allow_private,
                    headers=headers,
                    content=request.post_data_buffer,
                    max_bytes=self.max_response_bytes,
                    total_timeout=self.httpx_timeout,
                )
            except BlockedURLError:
                await route.abort("blockedbyclient")
                return
            except (ResponseTooLargeError, httpx.HTTPError) as exc:
                logger.info("Browser request %s failed: %s", url, type(exc).__name__)
                await route.abort("failed")
                return
            await route.fulfill(
                status=response.status_code,
                headers=_single_valued(response.headers.multi_items()),
                body=response.content,
            )

        return handle

    @staticmethod
    async def _refuse_websocket(ws: Any) -> None:
        """Scraping never needs WebSockets: close each one without connecting."""
        await ws.close(code=1008, reason="WebSockets are disabled by the scraper")

    @staticmethod
    def _looks_like_js_shell(html: str) -> bool:
        """Detect HTML that is a JS framework shell with no server-rendered content."""
        return any(pattern.search(html) for pattern in _JS_SHELL_PATTERNS)


def _single_valued(items: list[tuple[str, str]]) -> dict[str, str]:
    """Fold repeated headers for route.fulfill (Set-Cookie by newline, others by comma)."""
    folded: dict[str, str] = {}
    for key, value in items:
        name = key.lower()
        if name in folded:
            sep = "\n" if name == "set-cookie" else ", "
            folded[name] = folded[name] + sep + value
        else:
            folded[name] = value
    return folded
