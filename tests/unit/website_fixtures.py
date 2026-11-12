"""Offline sites for website-learning tests: nothing here touches the internet.

`site_transport(pages)` is an `httpx` transport that answers from a dict (and records every
request); `local_site(files)` is a real `ThreadingHTTPServer` on 127.0.0.1 for the one test that
proves the guarded client path end to end (it must pass `allow_private=True` explicitly).
"""

import inspect
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

import httpx

Page = httpx.Response | str | bytes | Callable[[httpx.Request], Any]


def html_page(title: str, body: str, *, head: str = "", links: tuple[str, ...] = ()) -> str:
    anchors = "".join(f'<a href="{href}">{href}</a> ' for href in links)
    return (
        f"<!doctype html><html><head><title>{title}</title>{head}</head><body>"
        f"<main><h1>{title}</h1><p>{body}</p><p>{anchors}</p></main></body></html>"
    )


def words(n: int, seed: str = "dental") -> str:
    """`n` distinct-ish words, so a page is not thin and two pages are not near-duplicates."""
    return " ".join(f"{seed}{i}" for i in range(n))


class SiteTransport(httpx.AsyncBaseTransport):
    """Answers requests from `pages`: keys are a full URL (no query) or a path for any host."""

    def __init__(self, pages: dict[str, Page]) -> None:
        self.pages = pages
        self.requests: list[httpx.Request] = []

    @property
    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    @property
    def urls(self) -> list[str]:
        return [str(r.url.copy_with(query=None, fragment=None)) for r in self.requests]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        full = str(request.url.copy_with(query=None, fragment=None))
        page = self.pages.get(full, self.pages.get(request.url.path))
        if page is None:
            return httpx.Response(404, text="not found", request=request)
        if callable(page) and not isinstance(page, httpx.Response):
            result = page(request)
            if inspect.isawaitable(result):
                result = await result
            page = result
        if isinstance(page, httpx.Response):
            page.request = request
            return page
        content = page.encode("utf-8") if isinstance(page, str) else page
        ctype = (
            "application/xml" if request.url.path.endswith(".xml") else "text/html; charset=utf-8"
        )
        return httpx.Response(
            200, content=content, headers={"content-type": ctype}, request=request
        )


def site_transport(pages: dict[str, Page]) -> SiteTransport:
    return SiteTransport(pages)


@contextmanager
def local_site(files: dict[str, Any]) -> Iterator[str]:
    """Serve `files` on 127.0.0.1. A value is a body (str/bytes), a `(status, headers, body)`
    tuple, or `("redirect", location)`."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            entry = files.get(urlsplit(self.path).path)
            if entry is None:
                self.send_response(404)
                self.end_headers()
                return
            if isinstance(entry, tuple) and entry[0] == "redirect":
                self.send_response(301)
                self.send_header("Location", entry[1])
                self.end_headers()
                return
            status, headers, body = (
                entry if isinstance(entry, tuple) else (200, {"Content-Type": "text/html"}, entry)
            )
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
