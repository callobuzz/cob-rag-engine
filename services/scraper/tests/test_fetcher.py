"""Tests for the hybrid page fetcher, including the SSRF guard on every hop."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pytest_httpx import HTTPXMock

from scraper.crawl.fetcher import PageFetcher
from scraper.crawl.netguard import make_client

BIG_HTML = "<html><body>" + "x" * 600 + "</body></html>"


@pytest.fixture
def fetcher() -> PageFetcher:
    """Create a PageFetcher with default settings (private networks blocked)."""
    return PageFetcher(httpx_timeout=10, playwright_timeout=30)


async def test_httpx_fetch_success(fetcher: PageFetcher, httpx_mock: HTTPXMock) -> None:
    """A successful httpx GET returns FetchResult with html and status 200."""
    httpx_mock.add_response(url="https://example.com", text=BIG_HTML)
    result = await fetcher.fetch("https://example.com")

    assert result.url == "https://example.com"
    assert result.status_code == 200
    assert result.method == "httpx"
    assert result.error is None
    assert len(result.html) > 0


async def test_httpx_404_returns_failed(fetcher: PageFetcher, httpx_mock: HTTPXMock) -> None:
    """A 404 response returns a FetchResult with an error message."""
    httpx_mock.add_response(url="https://example.com/missing", status_code=404, text="Not Found")
    result = await fetcher.fetch("https://example.com/missing")

    assert result.status_code == 404
    assert result.error == "HTTP 404"
    assert result.method == "httpx"


async def test_httpx_timeout_returns_error(fetcher: PageFetcher, httpx_mock: HTTPXMock) -> None:
    """A timeout from httpx returns a FetchResult with a Timeout error."""
    httpx_mock.add_exception(httpx.ReadTimeout("Connection timed out"))
    result = await fetcher.fetch("https://slow-site.com")

    assert result.status_code == 0
    assert result.error == "Timeout"
    assert result.method == "httpx"


async def test_public_redirect_is_followed(fetcher: PageFetcher, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/old", status_code=301, headers={"Location": "/new"}
    )
    httpx_mock.add_response(url="https://example.com/new", text=BIG_HTML)
    result = await fetcher.fetch("https://example.com/old")
    assert result.error is None
    assert result.status_code == 200


@pytest.mark.parametrize(
    "location",
    [
        "http://10.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8000/",
        "http://app:8000/api/v1/corpora",
        "https://rebind.example.com/",
    ],
)
async def test_redirect_to_private_is_blocked(
    fetcher: PageFetcher,
    httpx_mock: HTTPXMock,
    fake_dns: dict[str, list[str]],
    location: str,
) -> None:
    fake_dns["rebind.example.com"] = ["192.168.1.10"]
    httpx_mock.add_response(
        url="https://example.com/go", status_code=302, headers={"Location": location}
    )
    result = await fetcher.fetch("https://example.com/go")
    assert result.error is not None
    assert result.error.startswith("Blocked")
    # Exactly one request went out: the private hop was never requested.
    assert len(httpx_mock.get_requests()) == 1


async def test_too_many_redirects(fetcher: PageFetcher, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="https://example.com/loop",
        status_code=302,
        headers={"Location": "/loop"},
        is_reusable=True,
    )
    result = await fetcher.fetch("https://example.com/loop")
    assert result.error is not None
    assert len(httpx_mock.get_requests()) == 6


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.2.3.4/",
        "http://169.254.169.254/",
        "http://[::1]/",
        "http://app:8000/",
    ],
)
async def test_private_seed_is_never_requested(
    fetcher: PageFetcher, httpx_mock: HTTPXMock, url: str
) -> None:
    result = await fetcher.fetch(url)
    assert result.error is not None
    assert result.error.startswith("Blocked")
    assert httpx_mock.get_requests() == []


async def test_private_allowed_when_configured(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="http://10.2.3.4/", text=BIG_HTML)
    fetcher = PageFetcher(allow_private_networks=True)
    result = await fetcher.fetch("http://10.2.3.4/")
    assert result.error is None


async def test_httpx_path_refuses_oversized_response(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://example.com/big", content=b"x" * 2048)
    fetcher = PageFetcher(max_response_bytes=1024)
    result = await fetcher.fetch("https://example.com/big")
    assert result.error is not None
    assert "limit" in result.error


# ── Headless browser: every request is performed by the guarded client ──


def _route(url: str, method: str = "GET", body: bytes | None = None) -> MagicMock:
    route = MagicMock()
    route.request.url = url
    route.request.method = method
    route.request.headers = {
        "user-agent": "test",
        "accept-encoding": "gzip, br, zstd",
        "host": "spoofed",
    }
    route.request.post_data_buffer = body
    route.abort = AsyncMock()
    route.continue_ = AsyncMock()
    route.fulfill = AsyncMock()
    return route


@pytest.fixture
async def route_handler(fetcher: PageFetcher):
    async with make_client(False, 10) as client:
        yield fetcher._make_route_handler(client)


async def test_browser_request_is_fulfilled_by_us(
    route_handler, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://cdn.example.com/app.js",
        content=b"console.log(1)",
        headers={"Content-Type": "text/javascript", "X-Thing": "a"},
    )
    route = _route("https://cdn.example.com/app.js")
    await route_handler(route)

    route.continue_.assert_not_awaited()
    route.abort.assert_not_awaited()
    kwargs = route.fulfill.await_args.kwargs
    assert kwargs["status"] == 200
    assert kwargs["body"] == b"console.log(1)"
    assert kwargs["headers"]["content-type"] == "text/javascript"
    # The body handed back is the decoded one, so its framing headers describe it.
    assert kwargs["headers"]["content-length"] == str(len(b"console.log(1)"))
    sent = httpx_mock.get_requests()[0]
    assert sent.headers["user-agent"] == "test"
    assert sent.headers["host"] == "cdn.example.com"
    assert "br" not in sent.headers.get("accept-encoding", "")


async def test_browser_gets_decoded_body_without_content_encoding(
    fetcher: PageFetcher,
) -> None:
    """A gzip response is handed to the browser decoded, without Content-Encoding.

    Uses httpx.MockTransport: pytest-httpx cannot stream a gzip body.
    """
    import gzip

    def serve(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=gzip.compress(b"hello gzip"),
            headers={"Content-Encoding": "gzip", "Content-Type": "text/plain"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        route = _route("https://example.com/z")
        await fetcher._make_route_handler(client)(route)
    kwargs = route.fulfill.await_args.kwargs
    assert kwargs["body"] == b"hello gzip"
    assert "content-encoding" not in kwargs["headers"]
    assert kwargs["headers"]["content-length"] == str(len(b"hello gzip"))


async def test_browser_post_is_forwarded(route_handler, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://example.com/api", method="POST", json={"ok": True})
    route = _route("https://example.com/api", method="POST", body=b'{"q":1}')
    await route_handler(route)
    assert httpx_mock.get_requests()[0].content == b'{"q":1}'
    assert route.fulfill.await_args.kwargs["status"] == 200


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8000/",
        "http://app:8000/api/v1/corpora",
        "http://[::1]/",
    ],
)
async def test_browser_request_to_private_is_aborted(
    route_handler, httpx_mock: HTTPXMock, url: str
) -> None:
    route = _route(url)
    await route_handler(route)
    route.abort.assert_awaited_once_with("blockedbyclient")
    route.fulfill.assert_not_awaited()
    route.continue_.assert_not_awaited()
    assert httpx_mock.get_requests() == []


async def test_browser_name_resolving_private_is_aborted(
    route_handler, httpx_mock: HTTPXMock, fake_dns: dict[str, list[str]]
) -> None:
    fake_dns["rebind.example.com"] = ["10.9.8.7"]
    route = _route("https://rebind.example.com/x.js")
    await route_handler(route)
    route.abort.assert_awaited_once_with("blockedbyclient")
    assert httpx_mock.get_requests() == []


async def test_browser_redirect_to_private_is_aborted(
    route_handler, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://example.com/r", status_code=302,
        headers={"Location": "http://169.254.169.254/"},
    )
    route = _route("https://example.com/r")
    await route_handler(route)
    route.abort.assert_awaited_once_with("blockedbyclient")
    route.fulfill.assert_not_awaited()
    assert len(httpx_mock.get_requests()) == 1


async def test_browser_public_redirect_is_followed(
    route_handler, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="https://example.com/r", status_code=301, headers={"Location": "/final"}
    )
    httpx_mock.add_response(url="https://example.com/final", text="done")
    route = _route("https://example.com/r")
    await route_handler(route)
    assert route.fulfill.await_args.kwargs["body"] == b"done"


async def test_browser_oversized_response_is_aborted(httpx_mock: HTTPXMock) -> None:
    fetcher = PageFetcher(max_response_bytes=100)
    httpx_mock.add_response(url="https://example.com/huge", content=b"x" * 1000)
    async with make_client(False, 10) as client:
        route = _route("https://example.com/huge")
        await fetcher._make_route_handler(client)(route)
    route.abort.assert_awaited_once_with("failed")
    route.fulfill.assert_not_awaited()


async def test_browser_inline_urls_pass_and_other_schemes_abort(route_handler) -> None:
    for url in ("data:image/png;base64,AAAA", "blob:https://example.com/uuid"):
        route = _route(url)
        await route_handler(route)
        route.continue_.assert_awaited_once()
    for url in ("ftp://example.com/x", "file:///etc/passwd", "chrome-extension://abc/x"):
        route = _route(url)
        await route_handler(route)
        route.abort.assert_awaited_once_with("blockedbyclient")
        route.continue_.assert_not_awaited()


async def test_websockets_are_refused() -> None:
    ws = MagicMock()
    ws.close = AsyncMock()
    await PageFetcher._refuse_websocket(ws)
    ws.close.assert_awaited_once()
    assert ws.close.await_args.kwargs["code"] == 1008
    ws.connect_to_server.assert_not_called()


async def test_playwright_wiring(fetcher: PageFetcher, monkeypatch: pytest.MonkeyPatch) -> None:
    """The browser context blocks service workers and routes all requests and WebSockets."""
    page = MagicMock()
    page.goto = AsyncMock(return_value=MagicMock(status=200))
    page.content = AsyncMock(return_value="<html>rendered</html>")
    context = MagicMock()
    context.route = AsyncMock()
    context.route_web_socket = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()
    pw = MagicMock()
    pw.chromium.launch = AsyncMock(return_value=browser)
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=pw)
    manager.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: manager)

    result = await fetcher._fetch_playwright("https://example.com/")

    assert result.error is None
    assert result.html == "<html>rendered</html>"
    assert browser.new_context.await_args.kwargs["service_workers"] == "block"
    pattern, handler = context.route.await_args.args
    assert pattern == "**/*" and callable(handler)
    ws_pattern, ws_handler = context.route_web_socket.await_args.args
    assert ws_pattern.pattern == ".*"
    assert ws_handler == PageFetcher._refuse_websocket
    page.route.assert_not_called()
    browser.close.assert_awaited_once()


def test_chat_dashboard_no_longer_shows_crawl_method() -> None:
    index = Path(__file__).resolve().parents[2] / "chat" / "index.html"
    if not index.exists():
        pytest.skip("services/chat is not next to the scraper here")
    assert "crawl_method" not in index.read_text(encoding="utf-8")
