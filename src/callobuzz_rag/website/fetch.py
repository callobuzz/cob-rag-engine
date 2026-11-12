"""The website fetcher (BIZ1): one polite, guarded reader of one site.

Every request goes through `netguard.guarded_get` (address checks at check time and connect time,
manual redirects, size cap, total time budget) and a scope check on every hop, so a redirect to
another host is refused and never requested. robots.txt is fetched once per host and obeyed
(also for the hop a redirect lands on); the pause between two requests to a host is the larger of
`RAG_WEBSITE_REQUEST_DELAY_MS` and the robots `crawl-delay`. Conditional headers (`If-None-Match`,
`If-Modified-Since`) make an unchanged page a 304 with no body (the freshness checks rely on it).
A page that is an empty JavaScript shell goes to the scraper's browser when one is configured,
else it is reported as `needs_browser`.
"""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

import httpx

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.product import PUBLIC_DOCS_URL
from callobuzz_rag.fairuse.budget import FetchBudget, FetchBudgetExhausted  # PIP3 fair use
from callobuzz_rag.website import netguard
from callobuzz_rag.website.netguard import BlockedURLError, ResponseTooLargeError
from callobuzz_rag.website.render import render_configured, render_page
from callobuzz_rag.website.robots import Robots, robots_for_status
from callobuzz_rag.website.urls import canonical, in_scope, site_scope

logger = logging.getLogger(__name__)

DEFAULT_PUBLIC_URL: Final = f"{PUBLIC_DOCS_URL}/guide.html#sec-business"  # explains the crawler
_HTML_TYPES: Final = ("text/html", "application/xhtml+xml")
_SHELL_MARKERS: Final = [
    re.compile(rf"""<div\s+id=["']{name}["']\s*>\s*</div>""", re.IGNORECASE)
    for name in ("app", "root", "__next", "__nuxt")
]
_SHELL_MAX_BYTES: Final = 500


class _OffSiteError(BlockedURLError):
    """A redirect hop left the site."""


class _RobotsDisallowedError(BlockedURLError):
    """A redirect hop landed on a path robots.txt forbids."""


@dataclass(frozen=True)
class FetchOutcome:
    url: str  # final URL (canonical), after in-scope redirects
    status: int  # HTTP status; 0 = not requested or failed
    body: bytes | None  # None for 304, errors, skips
    content_type: str | None
    etag: str | None
    last_modified: str | None
    x_robots: frozenset[str]
    skip_reason: str | None  # robots_disallowed | redirect_off_site | blocked_address | too_large
    # | timeout | not_html | http_error | needs_browser | out_of_scope
    rendered: bool = False


def user_agent(settings: Settings) -> str:
    where = settings.public_url.strip() or DEFAULT_PUBLIC_URL
    return f"Mozilla/5.0 (compatible; {settings.website_bot_name}/1.0; +{where})"


def looks_like_js_shell(html: str) -> bool:
    """An (almost) empty page whose content a script is meant to build."""
    if any(marker.search(html) for marker in _SHELL_MARKERS):
        return True
    return len(html) < _SHELL_MAX_BYTES and "<script" in html.lower()


def _x_robots(headers: httpx.Headers, bot: str) -> frozenset[str]:
    """`noindex`/`nofollow` from X-Robots-Tag; a `agent: directive` value counts only for `*`
    or our own token."""
    found: set[str] = set()
    for value in headers.get_list("x-robots-tag"):
        for part in value.lower().split(","):
            token = part.strip()
            agent, sep, rest = token.partition(":")
            if sep:
                if agent.strip() not in ("*", bot.lower()):
                    continue
                token = rest.strip()
            if token in ("noindex", "nofollow"):
                found.add(token)
            elif token == "none":
                found.update(("noindex", "nofollow"))
    return frozenset(found)


class WebsiteFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        origin: str,
        transport: httpx.AsyncBaseTransport | None = None,
        allow_private: bool | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        render_transport: httpx.AsyncBaseTransport | None = None,
        scope: str | None = None,
        budget: "FetchBudget | None" = None,  # PIP3 fair use
    ) -> None:
        self._settings = settings
        self._origin = origin.rstrip("/")
        parts = urlsplit(self._origin)
        self._port = parts.port
        # `scope` is the validated scope the caller already holds (a website source's); without
        # it the scope is derived from the origin, which refuses IP literals and single labels.
        self._scope = scope if scope is not None else site_scope(self._origin)
        self._allow_private = (
            settings.website_allow_private_networks if allow_private is None else allow_private
        )
        self._timeout = float(settings.website_timeout_seconds)
        if transport is not None:
            self._client = httpx.AsyncClient(
                transport=transport, follow_redirects=False, timeout=httpx.Timeout(self._timeout)
            )
        else:
            self._client = netguard.make_client(self._allow_private, self._timeout)
        # PIP3 fair use: every real fetch spends the business's daily budget. The counter is a
        # request hook of the guarded client itself, run after the address and robots checks, so
        # a refused URL is neither fetched nor counted and no second client is ever opened.
        self._budget = budget
        if budget is not None:
            hooks = self._client.event_hooks
            hooks["request"].append(self._spend_fetch)
            self._client.event_hooks = hooks
        # /PIP3 fair use
        self._monotonic = monotonic
        self._sleep = sleep
        self._render_transport = render_transport
        self._robots: dict[str, Robots] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}
        self._pace_locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}
        self._headers = {"User-Agent": user_agent(settings), "Accept": "text/html,*/*;q=0.8"}
        self.files: set[str] = set()  # non-HTML documents seen (PDFs and the like)

    async def budget_limited(self) -> bool:  # PIP3 fair use
        """Whether this fetcher spends a limited daily budget (a run then reads new pages first)."""
        return self._budget is not None and await self._budget.is_limited()

    async def _spend_fetch(self, _request: httpx.Request) -> None:  # PIP3 fair use
        if self._budget is not None:
            await self._budget.spend()

    # -- robots ---------------------------------------------------------------------------

    async def robots(self) -> Robots:
        """The origin host's robots rules (fetched once)."""
        return await self._robots_for(urlsplit(self._origin).netloc.lower(), self._origin)

    async def _robots_for(self, netloc: str, base: str) -> Robots:
        cached = self._robots.get(netloc)
        if cached is not None:
            return cached
        lock = self._robots_locks.setdefault(netloc, asyncio.Lock())
        async with lock:
            cached = self._robots.get(netloc)
            if cached is not None:
                return cached
            status, text = 0, None
            try:
                await self._pace(netloc)
                response = await netguard.guarded_get(
                    self._client,
                    f"{base}/robots.txt",
                    self._allow_private,
                    headers=self._headers,
                    max_bytes=512 * 1024,
                    total_timeout=self._timeout,
                    check_hop=self._scope_hop,
                )
                status, text = response.status_code, response.text
            except _OffSiteError:
                logger.info("robots.txt of %s redirects off site: treated as disallow-all", netloc)
            except BlockedURLError:
                raise  # a private address is a refusal of the whole host, not "no robots.txt"
            except (ResponseTooLargeError, httpx.HTTPError):
                logger.info("robots.txt unreadable for %s: treated as disallow-all", netloc)
            rules = robots_for_status(status, text, bot=self._settings.website_bot_name)
            self._robots[netloc] = rules
            return rules

    async def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        rules = await self._robots_for(parts.netloc.lower(), base)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        return rules.allowed(target)

    # -- pacing, hops -----------------------------------------------------------------------

    async def _pace(self, netloc: str) -> None:
        lock = self._pace_locks.setdefault(netloc, asyncio.Lock())
        async with lock:
            delay = self._settings.website_request_delay_ms / 1000.0
            known = self._robots.get(netloc)
            if known is not None and known.crawl_delay is not None:
                delay = max(delay, known.crawl_delay)
            last = self._last_request.get(netloc)
            if last is not None:
                wait = delay - (self._monotonic() - last)
                if wait > 0:
                    await self._sleep(wait)
            self._last_request[netloc] = self._monotonic()

    async def _scope_hop(self, url: str) -> None:
        if not in_scope(url, self._scope, port=self._port):
            raise _OffSiteError(f"{url} is not on this site")

    async def _full_hop(self, url: str) -> None:
        await self._scope_hop(url)
        if not await self.allowed(url):
            raise _RobotsDisallowedError(f"{url} is disallowed by robots.txt")
        await self._pace(urlsplit(url).netloc.lower())

    # -- requests ---------------------------------------------------------------------------

    def _skip(self, url: str, reason: str, status: int = 0) -> FetchOutcome:
        return FetchOutcome(url, status, None, None, None, None, frozenset(), reason)

    async def _request(
        self, url: str, headers: dict[str, str], hop: Callable[[str], Awaitable[None]]
    ) -> tuple[httpx.Response | None, FetchOutcome | None]:
        try:
            response = await netguard.guarded_get(
                self._client,
                url,
                self._allow_private,
                headers=headers,
                max_bytes=self._settings.website_max_response_bytes,
                total_timeout=self._timeout,
                check_hop=hop,
            )
        except _OffSiteError:
            return None, self._skip(url, "redirect_off_site")
        except _RobotsDisallowedError:
            return None, self._skip(url, "robots_disallowed")
        except BlockedURLError:
            return None, self._skip(url, "blocked_address")
        except ResponseTooLargeError:
            return None, self._skip(url, "too_large")
        except httpx.TimeoutException:
            return None, self._skip(url, "timeout")
        except httpx.HTTPError:
            return None, self._skip(url, "http_error")
        return response, None

    def _outcome(
        self, response: httpx.Response, *, body: bytes | None, skip: str | None = None
    ) -> FetchOutcome:
        final = canonical(str(response.request.url)) or str(response.request.url)
        return FetchOutcome(
            url=final,
            status=response.status_code,
            body=body,
            content_type=response.headers.get("content-type"),
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
            x_robots=_x_robots(response.headers, self._settings.website_bot_name),
            skip_reason=skip,
        )

    async def get(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> FetchOutcome:
        """One HTML page. `etag`/`last_modified` make the request conditional (304 = no body)."""
        if not in_scope(url, self._scope, port=self._port):
            return self._skip(url, "out_of_scope")
        headers = dict(self._headers)
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        response, failure = await self._request(url, headers, self._full_hop)
        if failure is not None or response is None:
            return failure or self._skip(url, "http_error")
        if response.status_code == 304:
            return self._outcome(response, body=None)
        if response.status_code >= 400:
            return self._outcome(response, body=None, skip="http_error")
        ctype = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        if ctype and ctype not in _HTML_TYPES:
            self.files.add(canonical(str(response.request.url)) or url)
            return self._outcome(response, body=None, skip="not_html")
        text = response.text
        if looks_like_js_shell(text):
            return await self._rendered(response, url)
        return self._outcome(response, body=response.content)

    async def _rendered(self, response: httpx.Response, url: str) -> FetchOutcome:
        if not render_configured(self._settings):
            return self._outcome(response, body=None, skip="needs_browser")
        result = await render_page(
            self._settings, str(response.request.url), transport=self._render_transport
        )
        if result is None:
            return self._outcome(response, body=None, skip="needs_browser")
        if result.status >= 400:
            return self._skip(url, "http_error", result.status)
        final = canonical(result.url)
        if final is None or not in_scope(result.url, self._scope, port=self._port):
            return self._skip(url, "redirect_off_site", result.status)
        try:
            allowed = await self.allowed(final)
        except BlockedURLError:
            return self._skip(url, "blocked_address")
        if not allowed:
            return self._skip(url, "robots_disallowed")
        outcome = self._outcome(response, body=result.html.encode("utf-8"))
        return FetchOutcome(
            url=final,
            status=result.status,
            body=outcome.body,
            content_type="text/html",
            etag=None,
            last_modified=None,
            x_robots=outcome.x_robots,
            skip_reason=None,
            rendered=True,
        )

    async def get_document(
        self,
        url: str,
        *,
        etag: str | None = None,  # BIZ2 freshness: a conditional read of the sitemap
        last_modified: str | None = None,
    ) -> FetchOutcome:
        """A sitemap (any XML or gzip type). In scope and guarded like a page; robots.txt rules
        do not apply to the sitemap files robots.txt itself points at. `etag` and
        `last_modified` make the request conditional (304 = unchanged, no body)."""
        if not in_scope(url, self._scope, port=self._port):
            return self._skip(url, "out_of_scope")

        async def hop(target: str) -> None:
            await self._scope_hop(target)
            await self._pace(urlsplit(target).netloc.lower())

        headers = dict(self._headers)
        headers["Accept"] = "application/xml,text/xml,application/gzip,*/*;q=0.5"
        if etag:  # BIZ2 freshness
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        response, failure = await self._request(url, headers, hop)
        if failure is not None or response is None:
            return failure or self._skip(url, "http_error")
        if response.status_code >= 400:
            return self._outcome(response, body=None, skip="http_error")
        return self._outcome(
            response, body=None if response.status_code == 304 else response.content
        )

    async def aclose(self) -> None:
        await self._client.aclose()
        if self._budget is not None:  # PIP3 fair use: write what the run spent, return the rest
            await self._budget.close()


# PIP3 fair use
async def fetch_all(*calls: Awaitable[FetchOutcome]) -> list[FetchOutcome]:
    """Run reads together; the budget running out ends the group cleanly. Every read finishes
    first (none is left dangling), then `FetchBudgetExhausted` (or the first other error) is
    raised, so a caller can stop its run without losing track of a task."""
    results = await asyncio.gather(*calls, return_exceptions=True)
    outcomes: list[FetchOutcome] = []
    exhausted: FetchBudgetExhausted | None = None
    for result in results:
        if isinstance(result, FetchBudgetExhausted):
            exhausted = exhausted or result
        elif isinstance(result, BaseException):
            raise result
        else:
            outcomes.append(result)
    if exhausted is not None:
        raise exhausted
    return outcomes


__all__ = ["FetchBudgetExhausted", "WebsiteFetcher", "fetch_all"]
# /PIP3 fair use
