"""Tests for the crawl orchestrator."""

from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_httpx import HTTPXMock

from scraper.crawl.fetcher import FetchResult
from scraper.crawl.orchestrator import CrawlOrchestrator
from scraper.extract.extractor import ExtractionResult
from scraper.storage.database import Database
from scraper.storage.models import JobStatus, PageStatus, ScrapeJob


@pytest.fixture
async def db() -> Database:
    """Create a fresh in-memory database for each test."""
    database = Database(":memory:")
    await database.initialize()
    yield database  # type: ignore[misc]
    await database.close()


@pytest.fixture
def orchestrator(db: Database, tmp_path: Path) -> CrawlOrchestrator:
    """Create a CrawlOrchestrator with a temp data directory."""
    return CrawlOrchestrator(
        db=db,
        data_dir=str(tmp_path),
        concurrency=2,
        httpx_timeout=10,
        playwright_timeout=30,
        min_word_count=5,
    )


def _mock_fetch_and_extract(url: str) -> tuple[FetchResult, ExtractionResult]:
    """Return a fake fetch + extraction result for testing."""
    return (
        FetchResult(
            url=url,
            html="<html><body>Hello world content here</body></html>",
            method="httpx",
            status_code=200,
        ),
        ExtractionResult(
            content="Hello world content here for testing purposes with enough words",
            title="Test Page",
            word_count=11,
            content_hash=f"hash_{url}",
        ),
    )


async def test_crawl_job_creates_pages(
    orchestrator: CrawlOrchestrator, db: Database
) -> None:
    """Crawling a job creates page records in the database."""
    job = ScrapeJob(url="https://example.com", max_pages=5, id="cj_test_1234")
    await db.create_job(job)

    async def mock_fetch(url: str) -> tuple[FetchResult, ExtractionResult]:
        return _mock_fetch_and_extract(url)

    with (
        patch.object(orchestrator, "_fetch_and_extract", side_effect=mock_fetch),
        patch.object(orchestrator, "_resolve_urls", return_value=["https://example.com/a"]),
    ):
        await orchestrator.crawl(job)

    pages = await db.list_pages("cj_test_1234")
    assert len(pages) == 1
    assert pages[0].url == "https://example.com/a"
    assert pages[0].title == "Test Page"


async def test_crawl_saves_content_to_filesystem(
    orchestrator: CrawlOrchestrator, db: Database, tmp_path: Path
) -> None:
    """Crawling saves verified page content as .md files."""
    job = ScrapeJob(url="https://example.com", max_pages=5, id="cs_test_1234")
    await db.create_job(job)

    async def mock_fetch(url: str) -> tuple[FetchResult, ExtractionResult]:
        return _mock_fetch_and_extract(url)

    with (
        patch.object(orchestrator, "_fetch_and_extract", side_effect=mock_fetch),
        patch.object(orchestrator, "_resolve_urls", return_value=["https://example.com/b"]),
    ):
        await orchestrator.crawl(job)

    pages = await db.list_pages("cs_test_1234")
    assert len(pages) == 1

    md_file = tmp_path / "jobs" / "cs_test_1234" / f"{pages[0].id}.md"
    assert md_file.exists()
    content = md_file.read_text(encoding="utf-8")
    assert "Hello world" in content


async def test_crawl_updates_job_status(
    orchestrator: CrawlOrchestrator, db: Database
) -> None:
    """After crawling, the job status is updated to verifying (reviewing)."""
    job = ScrapeJob(url="https://example.com", max_pages=5, id="cu_test_1234")
    await db.create_job(job)

    async def mock_fetch(url: str) -> tuple[FetchResult, ExtractionResult]:
        return _mock_fetch_and_extract(url)

    with (
        patch.object(orchestrator, "_fetch_and_extract", side_effect=mock_fetch),
        patch.object(orchestrator, "_resolve_urls", return_value=["https://example.com/c"]),
    ):
        await orchestrator.crawl(job)

    updated_job = await db.get_job("cu_test_1234")
    assert updated_job is not None
    assert updated_job.status == JobStatus.VERIFYING
    assert updated_job.pages_done == 1


SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://site.example.com/s1</loc></url>
  <url><loc>https://site.example.com/a</loc></url>
  <url><loc>https://site.example.com/s2</loc></url>
</urlset>"""


async def test_resolve_urls_crawls_every_seed_then_sitemap(
    orchestrator: CrawlOrchestrator, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url="https://site.example.com/sitemap.xml", text=SITEMAP)
    job = ScrapeJob(
        url="https://site.example.com/a",
        seed_urls=["https://site.example.com/a", "https://site.example.com/b"],
        sitemap_url="https://site.example.com/sitemap.xml",
        max_pages=4,
    )
    urls = await orchestrator._resolve_urls(job)
    assert urls == [
        "https://site.example.com/a",
        "https://site.example.com/b",
        "https://site.example.com/s1",
        "https://site.example.com/s2",
    ]


async def test_resolve_urls_seeds_only_does_not_guess_sitemap(
    orchestrator: CrawlOrchestrator, httpx_mock: HTTPXMock
) -> None:
    job = ScrapeJob(
        url="https://site.example.com/a",
        seed_urls=["https://site.example.com/a", "https://site.example.com/b"],
    )
    assert await orchestrator._resolve_urls(job) == job.seed_urls
    assert httpx_mock.get_requests() == []


async def test_resolve_urls_caps_at_max_pages(
    orchestrator: CrawlOrchestrator, httpx_mock: HTTPXMock
) -> None:
    job = ScrapeJob(
        url="https://site.example.com/1",
        seed_urls=[f"https://site.example.com/{i}" for i in range(5)],
        sitemap_url="https://site.example.com/sitemap.xml",
        max_pages=3,
    )
    assert len(await orchestrator._resolve_urls(job)) == 3
    # Seeds already fill max_pages, so the sitemap is not fetched.
    assert httpx_mock.get_requests() == []


async def test_private_sitemap_is_not_fetched(
    orchestrator: CrawlOrchestrator, httpx_mock: HTTPXMock
) -> None:
    job = ScrapeJob(url="http://169.254.169.254/", sitemap_url="http://169.254.169.254/x.xml")
    assert await orchestrator._resolve_urls(job) == []
    assert httpx_mock.get_requests() == []


async def test_sitemap_urls_pointing_inside_are_refused_at_fetch(
    orchestrator: CrawlOrchestrator, db: Database, httpx_mock: HTTPXMock
) -> None:
    """A public sitemap listing an internal URL: that page fails, nothing is requested."""
    httpx_mock.add_response(
        url="https://site.example.com/sitemap.xml",
        text=SITEMAP.replace("https://site.example.com/s1", "http://app:8000/api/v1/corpora"),
    )
    httpx_mock.add_response(text="<html>" + "word " * 200 + "</html>", is_reusable=True)
    job = ScrapeJob(
        url="https://site.example.com/sitemap.xml",
        sitemap_url="https://site.example.com/sitemap.xml",
        max_pages=10,
        id="ssrf_job_123",
    )
    await db.create_job(job)
    await orchestrator.crawl(job)
    pages = {p.url: p for p in await db.list_pages(job.id)}
    assert pages["http://app:8000/api/v1/corpora"].status == PageStatus.FAILED
    assert all(r.url.host != "app" for r in httpx_mock.get_requests())


async def test_invalid_sitemap_xml_is_ignored(
    orchestrator: CrawlOrchestrator, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url="https://site.example.com/sitemap.xml", text="<html>nope")
    job = ScrapeJob(
        url="https://site.example.com/a",
        seed_urls=["https://site.example.com/a"],
        sitemap_url="https://site.example.com/sitemap.xml",
    )
    assert await orchestrator._resolve_urls(job) == ["https://site.example.com/a"]
