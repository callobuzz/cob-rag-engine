import asyncio

import httpx
import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.website import netguard
from callobuzz_rag.website.fetch import WebsiteFetcher, looks_like_js_shell
from tests.unit.website_fixtures import html_page, local_site, site_transport, words

ORIGIN = "https://shreedental.in"
PAGE = html_page("Pricing", words(80))


def settings(**kw: object) -> Settings:
    return Settings(_env_file=None, website_request_delay_ms=0, **kw)  # type: ignore[call-arg]


def fetcher(pages: dict, **kw: object) -> tuple[WebsiteFetcher, object]:
    transport = site_transport(pages)
    opts = {"allow_private": False, **kw}
    return WebsiteFetcher(settings(), origin=ORIGIN, transport=transport, **opts), transport  # type: ignore[arg-type]


async def test_user_agent_and_robots_fetched_once() -> None:
    f, t = fetcher({"/robots.txt": "User-agent: *\nDisallow:\n", "/a": PAGE, "/b": PAGE})
    await f.get(f"{ORIGIN}/a")
    await f.get(f"{ORIGIN}/b")
    assert t.paths.count("/robots.txt") == 1
    ua = t.requests[0].headers["user-agent"]
    assert ua.startswith("Mozilla/5.0 (compatible; CobRagBot/1.0; +") and ua.endswith(")")


async def test_a_robots_disallowed_url_is_never_requested() -> None:
    f, t = fetcher({"/robots.txt": "User-agent: *\nDisallow: /private/\n", "/private/x": PAGE})
    out = await f.get(f"{ORIGIN}/private/x")
    assert out.skip_reason == "robots_disallowed" and out.body is None
    assert t.paths == ["/robots.txt"]


async def test_robots_5xx_means_nothing_is_read() -> None:
    f, t = fetcher({"/robots.txt": httpx.Response(503), "/a": PAGE})
    assert (await f.get(f"{ORIGIN}/a")).skip_reason == "robots_disallowed"
    assert t.paths == ["/robots.txt"]


async def test_redirect_to_another_host_is_refused_and_never_requested() -> None:
    pages = {
        "/robots.txt": "",
        "/old": httpx.Response(301, headers={"location": "https://evil.example/x"}),
        "https://evil.example/x": PAGE,
    }
    f, t = fetcher(pages)
    out = await f.get(f"{ORIGIN}/old")
    assert out.skip_reason == "redirect_off_site"
    assert all(u.startswith(ORIGIN) for u in t.urls)


async def test_in_scope_redirect_is_followed_and_reports_the_final_url() -> None:
    pages = {
        "/robots.txt": "",
        "/old": httpx.Response(301, headers={"location": "https://www.shreedental.in/new"}),
        "/new": PAGE,
    }
    f, _ = fetcher(pages)
    out = await f.get(f"{ORIGIN}/old")
    assert out.url == "https://www.shreedental.in/new" and out.body and out.skip_reason is None


async def test_a_redirect_onto_a_robots_forbidden_path_is_not_followed() -> None:
    pages = {
        "/robots.txt": "User-agent: *\nDisallow: /secret\n",
        "/go": httpx.Response(302, headers={"location": "/secret"}),
        "/secret": PAGE,
    }
    f, t = fetcher(pages)
    assert (await f.get(f"{ORIGIN}/go")).skip_reason == "robots_disallowed"
    assert "/secret" not in t.paths


async def test_a_hop_resolving_to_a_private_address_is_blocked(monkeypatch) -> None:
    async def fake(host: str, port: int) -> list[str]:
        return ["10.0.0.2"] if host == "shreedental.in" else ["93.184.216.34"]

    monkeypatch.setattr(netguard, "resolve_host", fake)
    f, t = fetcher({"/robots.txt": "", "/a": PAGE})
    out = await f.get(f"{ORIGIN}/a")
    assert out.skip_reason == "blocked_address"
    assert t.requests == []


async def test_pdf_is_not_html_and_is_recorded() -> None:
    pdf = httpx.Response(200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"})
    f, _ = fetcher({"/robots.txt": "", "/brochure.pdf": pdf})
    out = await f.get(f"{ORIGIN}/brochure.pdf")
    assert out.skip_reason == "not_html" and out.body is None
    assert f.files == {f"{ORIGIN}/brochure.pdf"}


async def test_body_over_the_cap_and_slow_server() -> None:
    big = "x" * 4096
    transport = site_transport({"/robots.txt": "", "/big": big})
    f = WebsiteFetcher(
        Settings(_env_file=None, website_request_delay_ms=0, website_max_response_bytes=1024),  # type: ignore[call-arg]
        origin=ORIGIN,
        transport=transport,
        allow_private=False,
    )
    assert (await f.get(f"{ORIGIN}/big")).skip_reason == "too_large"

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, text=PAGE)

    transport = site_transport({"/robots.txt": "", "/slow": slow})
    f = WebsiteFetcher(
        Settings(_env_file=None, website_request_delay_ms=0, website_timeout_seconds=1),  # type: ignore[call-arg]
        origin=ORIGIN,
        transport=transport,
        allow_private=False,
    )
    assert (await f.get(f"{ORIGIN}/slow")).skip_reason == "timeout"


async def test_conditional_headers_and_304() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(304, headers={"etag": '"v1"'})

    f, _ = fetcher({"/robots.txt": "", "/a": handler})
    out = await f.get(f"{ORIGIN}/a", etag='"v1"', last_modified="Mon, 05 Oct 2026 10:00:00 GMT")
    assert out.status == 304 and out.body is None and out.skip_reason is None
    assert seen["if-none-match"] == '"v1"'
    assert seen["if-modified-since"] == "Mon, 05 Oct 2026 10:00:00 GMT"


async def test_validators_and_x_robots_are_reported() -> None:
    resp = httpx.Response(
        200,
        text=PAGE,
        headers={
            "content-type": "text/html",
            "etag": '"abc"',
            "last-modified": "Mon, 05 Oct 2026 10:00:00 GMT",
            "x-robots-tag": "noindex, nofollow",
        },
    )
    f, _ = fetcher({"/robots.txt": "", "/a": resp})
    out = await f.get(f"{ORIGIN}/a")
    assert out.etag == '"abc"' and out.last_modified and out.x_robots == {"noindex", "nofollow"}


async def test_x_robots_for_another_agent_is_ignored() -> None:
    resp = httpx.Response(
        200, text=PAGE, headers={"content-type": "text/html", "x-robots-tag": "googlebot: noindex"}
    )
    f, _ = fetcher({"/robots.txt": "", "/a": resp})
    assert (await f.get(f"{ORIGIN}/a")).x_robots == frozenset()


async def test_the_pause_between_requests_and_crawl_delay() -> None:
    clock = [0.0]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock[0] += seconds

    def make(robots: str) -> WebsiteFetcher:
        return WebsiteFetcher(
            Settings(_env_file=None, website_request_delay_ms=500),  # type: ignore[call-arg]
            origin=ORIGIN,
            transport=site_transport({"/robots.txt": robots, "/a": PAGE, "/b": PAGE}),
            allow_private=False,
            monotonic=lambda: clock[0],
            sleep=sleep,
        )

    f = make("")
    await f.get(f"{ORIGIN}/a")
    await f.get(f"{ORIGIN}/b")
    assert slept and all(abs(s - 0.5) < 1e-9 for s in slept)
    slept.clear()
    f = make("User-agent: *\nCrawl-delay: 3\n")
    await f.get(f"{ORIGIN}/a")
    await f.get(f"{ORIGIN}/b")
    assert max(slept) == pytest.approx(3.0)


SHELL = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'


def test_shell_detection() -> None:
    assert looks_like_js_shell(SHELL)
    assert looks_like_js_shell("<html><body><script>x()</script></body></html>")
    assert not looks_like_js_shell(PAGE)
    assert not looks_like_js_shell("<html><body><p>Hi</p></body></html>")


async def test_js_shell_without_a_render_service_needs_a_browser() -> None:
    f, _ = fetcher({"/robots.txt": "", "/app": SHELL})
    out = await f.get(f"{ORIGIN}/app")
    assert out.skip_reason == "needs_browser" and out.body is None


async def test_js_shell_with_a_render_service_uses_it() -> None:
    calls: list[httpx.Request] = []

    def render(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"url": f"{ORIGIN}/app", "status_code": 200, "html": PAGE})

    render_t = site_transport({"/api/v1/render": render})
    f = WebsiteFetcher(
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            website_request_delay_ms=0,
            website_render_url="http://scraper:8001",
            website_render_token="s3cret",
        ),
        origin=ORIGIN,
        transport=site_transport({"/robots.txt": "", "/app": SHELL}),
        allow_private=False,
        render_transport=render_t,
    )
    out = await f.get(f"{ORIGIN}/app")
    assert out.rendered and out.body and b"Pricing" in out.body
    assert len(calls) == 1
    assert calls[0].headers["x-render-token"] == "s3cret"
    assert calls[0].url.host == "scraper" and b"shreedental.in/app" in calls[0].content

    # a render answer for a final URL on another site is refused
    render_t.pages["/api/v1/render"] = httpx.Response(
        200, json={"url": "https://evil.example/", "status_code": 200, "html": PAGE}
    )
    assert (await f.get(f"{ORIGIN}/app")).skip_reason == "redirect_off_site"


async def test_get_document_serves_sitemaps_and_refuses_other_hosts() -> None:
    f, _ = fetcher({"/robots.txt": "User-agent: *\nDisallow: /\n", "/sitemap.xml": "<urlset/>"})
    out = await f.get_document(f"{ORIGIN}/sitemap.xml")  # robots rules do not apply to sitemaps
    assert out.body == b"<urlset/>"
    assert (await f.get_document("https://evil.example/sitemap.xml")).skip_reason == "out_of_scope"


async def test_real_sockets_through_the_guarded_client() -> None:
    robots = "User-agent: *\nDisallow:\n"
    with local_site(
        {
            "/robots.txt": robots,
            "/": html_page("Home", words(60)),
            "/old": ("redirect", "/new"),
            "/new": html_page("New", words(60, "x")),
        }
    ) as base:
        f = WebsiteFetcher(
            Settings(_env_file=None, website_request_delay_ms=0),  # type: ignore[call-arg]
            origin=base,
            scope="127.0.0.1",
            allow_private=True,  # set explicitly: the fixture server is on 127.0.0.1
        )
        try:
            home = await f.get(f"{base}/")
            moved = await f.get(f"{base}/old")
        finally:
            await f.aclose()
    assert home.status == 200 and home.body
    assert moved.url == f"{base}/new" and moved.body


async def test_private_addresses_are_refused_unless_allowed() -> None:
    with local_site({"/robots.txt": "", "/": html_page("Home", words(60))}) as base:
        f = WebsiteFetcher(Settings(_env_file=None), origin=base, scope="127.0.0.1")  # type: ignore[call-arg]
        try:
            out = await f.get(f"{base}/")
        finally:
            await f.aclose()
    assert out.skip_reason in {"robots_disallowed", "blocked_address"} and out.body is None


async def test_a_rendered_error_page_is_not_content() -> None:
    def render(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"url": f"{ORIGIN}/app", "status_code": 404, "html": PAGE})

    f = WebsiteFetcher(
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            website_request_delay_ms=0,
            website_render_url="http://scraper:8001",
            website_render_token="s3cret",
        ),
        origin=ORIGIN,
        transport=site_transport({"/robots.txt": "", "/app": SHELL}),
        allow_private=False,
        render_transport=site_transport({"/api/v1/render": render}),
    )
    out = await f.get(f"{ORIGIN}/app")
    assert (out.skip_reason, out.status, out.body) == ("http_error", 404, None)
