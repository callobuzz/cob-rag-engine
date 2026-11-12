"""Tests for scraper data models."""

from scraper.storage.models import JobStatus, PageStatus, ScrapedPage, ScrapeJob


def test_create_job() -> None:
    """ScrapeJob initializes with correct defaults."""
    job = ScrapeJob(url="https://example.com", max_pages=10)
    assert job.url == "https://example.com"
    assert job.max_pages == 10
    assert job.status == JobStatus.PENDING
    assert job.pages_found == 0
    assert job.pages_done == 0
    assert job.error is None
    assert len(job.id) == 12


def test_job_to_dict() -> None:
    """ScrapeJob.to_dict() returns all fields with correct types."""
    job = ScrapeJob(url="https://example.com", id="abc123def456")
    d = job.to_dict()
    assert d["id"] == "abc123def456"
    assert d["url"] == "https://example.com"
    assert d["status"] == "pending"
    assert isinstance(d["max_pages"], int)
    assert isinstance(d["created_at"], str)


def test_create_page() -> None:
    """ScrapedPage initializes with correct defaults."""
    page = ScrapedPage(job_id="job1", url="https://example.com/page")
    assert page.job_id == "job1"
    assert page.url == "https://example.com/page"
    assert page.status == PageStatus.QUEUED
    assert page.word_count == 0
    assert page.content_md == ""
    assert page.rejection_reason is None
    assert len(page.id) == 12


def test_page_to_dict() -> None:
    """ScrapedPage.to_dict() returns all fields with correct types."""
    page = ScrapedPage(job_id="job1", url="https://example.com/page", id="page123456ab")
    d = page.to_dict()
    assert d["id"] == "page123456ab"
    assert d["job_id"] == "job1"
    assert d["status"] == "queued"
    assert isinstance(d["word_count"], int)
    assert d["fetched_at"] is None
