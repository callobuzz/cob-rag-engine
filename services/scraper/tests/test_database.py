"""Tests for the async SQLite database layer."""

from pathlib import Path

import aiosqlite
import pytest

from scraper.storage.database import Database
from scraper.storage.models import JobStatus, PageStatus, ScrapedPage, ScrapeJob


@pytest.fixture
async def db(tmp_path: object) -> Database:  # type: ignore[override]
    """Create a fresh in-memory database for each test."""
    database = Database(":memory:")
    await database.initialize()
    yield database  # type: ignore[misc]
    await database.close()


async def test_create_and_get_job(db: Database) -> None:
    """Round-trip: create a job then retrieve it by ID."""
    job = ScrapeJob(url="https://example.com", max_pages=10, id="testjob12345")
    await db.create_job(job)
    fetched = await db.get_job("testjob12345")
    assert fetched is not None
    assert fetched.url == "https://example.com"
    assert fetched.max_pages == 10
    assert fetched.status == JobStatus.PENDING


async def test_list_jobs(db: Database) -> None:
    """list_jobs returns all jobs ordered by creation time."""
    await db.create_job(ScrapeJob(url="https://a.com", id="job_a_123456"))
    await db.create_job(ScrapeJob(url="https://b.com", id="job_b_123456"))
    jobs = await db.list_jobs()
    assert len(jobs) == 2


async def test_update_job(db: Database) -> None:
    """Updating a job persists the new status."""
    job = ScrapeJob(url="https://example.com", id="upd_job_1234")
    await db.create_job(job)
    job.status = JobStatus.CRAWLING
    job.pages_found = 5
    await db.update_job(job)
    fetched = await db.get_job("upd_job_1234")
    assert fetched is not None
    assert fetched.status == JobStatus.CRAWLING
    assert fetched.pages_found == 5


async def test_delete_job(db: Database) -> None:
    """Deleting a job also removes its pages."""
    job = ScrapeJob(url="https://example.com", id="del_job_1234")
    await db.create_job(job)
    page = ScrapedPage(job_id="del_job_1234", url="https://example.com/p1", id="del_page_123")
    await db.create_page(page)
    await db.delete_job("del_job_1234")
    assert await db.get_job("del_job_1234") is None
    assert await db.get_page("del_page_123") is None


async def test_create_and_get_page(db: Database) -> None:
    """Round-trip: create a page then retrieve it."""
    job = ScrapeJob(url="https://example.com", id="pg_job_12345")
    await db.create_job(job)
    page = ScrapedPage(job_id="pg_job_12345", url="https://example.com/about", id="pg_page_1234")
    await db.create_page(page)
    fetched = await db.get_page("pg_page_1234")
    assert fetched is not None
    assert fetched.url == "https://example.com/about"
    assert fetched.status == PageStatus.QUEUED


async def test_list_pages(db: Database) -> None:
    """list_pages returns all pages for a job."""
    job = ScrapeJob(url="https://example.com", id="lp_job_12345")
    await db.create_job(job)
    await db.create_page(ScrapedPage(job_id="lp_job_12345", url="https://example.com/1"))
    await db.create_page(ScrapedPage(job_id="lp_job_12345", url="https://example.com/2"))
    pages = await db.list_pages("lp_job_12345")
    assert len(pages) == 2


async def test_list_pages_with_status_filter(db: Database) -> None:
    """list_pages filters by status when provided."""
    job = ScrapeJob(url="https://example.com", id="flt_job_1234")
    await db.create_job(job)
    p1 = ScrapedPage(job_id="flt_job_1234", url="https://example.com/1", id="flt_page_123")
    p2 = ScrapedPage(job_id="flt_job_1234", url="https://example.com/2", id="flt_page_456")
    await db.create_page(p1)
    await db.create_page(p2)
    p1.status = PageStatus.FETCHED
    await db.update_page(p1)
    queued = await db.list_pages("flt_job_1234", status=PageStatus.QUEUED)
    fetched = await db.list_pages("flt_job_1234", status=PageStatus.FETCHED)
    assert len(queued) == 1
    assert len(fetched) == 1


async def test_update_page(db: Database) -> None:
    """Updating a page persists changes."""
    job = ScrapeJob(url="https://example.com", id="up_job_12345")
    await db.create_job(job)
    page = ScrapedPage(job_id="up_job_12345", url="https://example.com/x", id="up_page_1234")
    await db.create_page(page)
    page.status = PageStatus.EXTRACTED
    page.title = "Example Page"
    page.word_count = 500
    await db.update_page(page)
    fetched = await db.get_page("up_page_1234")
    assert fetched is not None
    assert fetched.status == PageStatus.EXTRACTED
    assert fetched.title == "Example Page"
    assert fetched.word_count == 500


async def test_count_pages_by_status(db: Database) -> None:
    """count_pages_by_status returns correct counts per status."""
    job = ScrapeJob(url="https://example.com", id="cnt_job_1234")
    await db.create_job(job)
    p1 = ScrapedPage(job_id="cnt_job_1234", url="https://example.com/1", id="cnt_page_123")
    p2 = ScrapedPage(job_id="cnt_job_1234", url="https://example.com/2", id="cnt_page_456")
    p3 = ScrapedPage(job_id="cnt_job_1234", url="https://example.com/3", id="cnt_page_789")
    await db.create_page(p1)
    await db.create_page(p2)
    await db.create_page(p3)
    p1.status = PageStatus.FETCHED
    p2.status = PageStatus.FETCHED
    await db.update_page(p1)
    await db.update_page(p2)
    counts = await db.count_pages_by_status("cnt_job_1234")
    assert counts.get("fetched") == 2
    assert counts.get("queued") == 1


OLD_JOBS_TABLE = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    max_pages INTEGER NOT NULL DEFAULT 50,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    pages_found INTEGER NOT NULL DEFAULT 0,
    pages_done INTEGER NOT NULL DEFAULT 0,
    error TEXT
)
"""


OLD_PAGES_TABLE = """
CREATE TABLE pages (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    title TEXT NOT NULL DEFAULT '',
    word_count INTEGER NOT NULL DEFAULT 0,
    content_md TEXT NOT NULL DEFAULT '',
    content_html TEXT NOT NULL DEFAULT '',
    rejection_reason TEXT,
    fetched_at TEXT,
    extracted_at TEXT
)
"""


async def test_initialize_migrates_old_jobs_table(tmp_path: Path) -> None:
    """An existing SQLite file from before job ownership gains the new columns."""
    path = str(tmp_path / "old.db")
    async with aiosqlite.connect(path) as conn:
        await conn.execute(OLD_JOBS_TABLE)
        await conn.execute(OLD_PAGES_TABLE)
        await conn.execute(
            "INSERT INTO pages (id, job_id, url, status) VALUES (?, ?, ?, ?)",
            ("legacypage12", "legacyjob123", "https://old.example.com/p", "delivered"),
        )
        await conn.execute(
            "INSERT INTO jobs (id, url, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("legacyjob123", "https://old.example.com", "2026-01-01", "2026-01-01"),
        )
        await conn.commit()

    database = Database(path)
    await database.initialize()
    try:
        cursor = await database.db.execute("PRAGMA table_info(jobs)")
        columns = {row["name"] for row in await cursor.fetchall()}
        assert {
            "target_corpus_id",
            "owner_tenant_id",
            "review_mode",
            "seed_urls",
            "sitemap_url",
        } <= columns
        cursor = await database.db.execute("PRAGMA table_info(pages)")
        assert "delivery_error" in {row["name"] for row in await cursor.fetchall()}

        old_page = await database.get_page("legacypage12")
        assert old_page is not None
        assert old_page.status == PageStatus.DELIVERED  # existing rows stay as they are
        assert old_page.delivery_error is None

        legacy = await database.get_job("legacyjob123")
        assert legacy is not None
        assert legacy.target_corpus_id == ""
        assert legacy.owner_tenant_id is None
        assert legacy.review_mode is True
        assert (legacy.seed_urls, legacy.sitemap_url) == ([], None)

        new = ScrapeJob(
            url="https://new.example.com",
            target_corpus_id="c1",
            owner_tenant_id="t1",
            review_mode=False,
            seed_urls=["https://new.example.com/a", "https://new.example.com/b"],
            sitemap_url="https://new.example.com/sitemap.xml",
        )
        await database.create_job(new)
        fetched = await database.get_job(new.id)
        assert fetched is not None
        assert (fetched.target_corpus_id, fetched.owner_tenant_id, fetched.review_mode) == (
            "c1",
            "t1",
            False,
        )
        assert fetched.seed_urls == new.seed_urls
        assert fetched.sitemap_url == new.sitemap_url
    finally:
        await database.close()

    # Initializing again is a no-op.
    again = Database(path)
    await again.initialize()
    await again.close()
