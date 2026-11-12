"""POST /api/v1/render: the engine's door to the headless browser (BIZ1). No browser runs here:
the fetcher's Playwright path is replaced."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from scraper.api.app import create_app
from scraper.config import ScraperSettings
from scraper.crawl.fetcher import FetchResult, PageFetcher
from scraper.storage.database import Database

TOKEN = "render-secret-token"


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    # Local copy of the shared fixture: this file also runs where pytest-httpx (imported by
    # conftest.py) is not installed.
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database
    await database.close()


async def _client(db: Database, tmp_path: Path, token: str) -> AsyncIterator[AsyncClient]:
    settings = ScraperSettings(
        data_dir=str(tmp_path / "data"),
        db_path=str(tmp_path / "t.db"),
        render_token=token,
        max_response_bytes=2000,
    )
    app = create_app(db=db, data_dir=settings.data_dir, settings=settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def on(db: Database, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    async for c in _client(db, tmp_path, TOKEN):
        yield c


@pytest.fixture
async def off(db: Database, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    async for c in _client(db, tmp_path, ""):
        yield c


@pytest.fixture(autouse=True)
def fake_browser(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def fake(self: PageFetcher, url: str) -> FetchResult:
        calls.append(url)
        return FetchResult(
            url=url,
            html="<html><body>रूट कैनाल ₹4,500</body></html>",
            method="playwright",
            status_code=200,
            final_url=url + "#done",
        )

    monkeypatch.setattr(PageFetcher, "_fetch_playwright", fake)
    return calls


async def test_no_token_configured_means_no_route(
    off: AsyncClient, fake_browser: list[str]
) -> None:
    resp = await off.post(
        "/api/v1/render", json={"url": "https://shreedental.in/"}, headers={"X-Render-Token": TOKEN}
    )
    assert resp.status_code == 404
    assert fake_browser == []


@pytest.mark.parametrize("headers", [{}, {"X-Render-Token": "wrong"}, {"X-Render-Token": ""}])
async def test_missing_or_wrong_token_is_401(
    on: AsyncClient, fake_browser: list[str], headers: dict[str, str]
) -> None:
    resp = await on.post("/api/v1/render", json={"url": "https://shreedental.in/"}, headers=headers)
    assert resp.status_code == 401
    assert fake_browser == []


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8000/", "http://app:8000/", "http://169.254.169.254/", "file:///etc/passwd"],
)
async def test_private_urls_are_refused(on: AsyncClient, fake_browser: list[str], url: str) -> None:
    resp = await on.post("/api/v1/render", json={"url": url}, headers={"X-Render-Token": TOKEN})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "blocked"
    assert fake_browser == []


async def test_good_call_returns_the_rendered_page(
    on: AsyncClient, fake_browser: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def public(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr("scraper.crawl.netguard.resolve_host", public)
    resp = await on.post(
        "/api/v1/render",
        json={"url": "https://shreedental.in/app"},
        headers={"X-Render-Token": TOKEN},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status_code"] == 200
    assert body["url"] == "https://shreedental.in/app#done"
    assert "₹4,500" in body["html"]
    assert fake_browser == ["https://shreedental.in/app"]


async def test_html_is_capped_at_the_response_limit(
    on: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def public(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    async def big(self: PageFetcher, url: str) -> FetchResult:
        return FetchResult(url=url, html="x" * 10_000, method="playwright", status_code=200)

    monkeypatch.setattr("scraper.crawl.netguard.resolve_host", public)
    monkeypatch.setattr(PageFetcher, "_fetch_playwright", big)
    resp = await on.post(
        "/api/v1/render", json={"url": "https://shreedental.in/"}, headers={"X-Render-Token": TOKEN}
    )
    assert resp.status_code == 200 and len(resp.json()["html"]) == 2000


async def test_a_failed_render_is_a_502(on: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def public(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    async def broken(self: PageFetcher, url: str) -> FetchResult:
        return FetchResult(url=url, html="", method="playwright", status_code=0, error="boom")

    monkeypatch.setattr("scraper.crawl.netguard.resolve_host", public)
    monkeypatch.setattr(PageFetcher, "_fetch_playwright", broken)
    resp = await on.post(
        "/api/v1/render", json={"url": "https://shreedental.in/"}, headers={"X-Render-Token": TOKEN}
    )
    assert resp.status_code == 502
    assert "boom" not in resp.text  # nothing internal leaks to the caller


async def test_unknown_fields_are_refused(on: AsyncClient) -> None:
    resp = await on.post(
        "/api/v1/render",
        json={"url": "https://shreedental.in/", "x": 1},
        headers={"X-Render-Token": TOKEN},
    )
    assert resp.status_code == 422
