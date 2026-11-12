"""Data models for scrape jobs and pages."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class JobStatus(StrEnum):
    """Scrape job lifecycle states."""

    PENDING = "pending"
    CRAWLING = "crawling"
    EXTRACTING = "extracting"
    VERIFYING = "verifying"
    DELIVERING = "delivering"
    COMPLETED = "completed"
    FAILED = "failed"


class PageStatus(StrEnum):
    """Individual page processing states."""

    QUEUED = "queued"
    FETCHED = "fetched"
    EXTRACTED = "extracted"
    VERIFIED = "verified"
    REJECTED = "rejected"
    APPROVED = "approved"  # a reviewer (or review_mode=false) cleared it for delivery
    DELIVERED = "delivered"  # the engine accepted it
    FAILED = "failed"


@dataclass
class ScrapeJob:
    """A scrape job targeting one root URL."""

    url: str
    max_pages: int = 50
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    status: JobStatus = JobStatus.PENDING
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    pages_found: int = 0
    pages_done: int = 0
    error: str | None = None
    # Ownership: the engine corpus pages are delivered to, and the tenant that
    # submitted the job (None for an admin key). Jobs from before these columns
    # existed have target_corpus_id "" and are visible to an admin only.
    target_corpus_id: str = ""
    owner_tenant_id: str | None = None
    # False: verified pages are approved and delivered as soon as the crawl ends.
    review_mode: bool = True
    # What was submitted: every URL is crawled, then the sitemap's URLs, up to
    # max_pages. ``url`` stays the display root (first seed, else the sitemap).
    seed_urls: list[str] = field(default_factory=list)
    sitemap_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for API responses and DB storage."""
        return {
            "id": self.id,
            "url": self.url,
            "max_pages": self.max_pages,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "pages_found": self.pages_found,
            "pages_done": self.pages_done,
            "error": self.error,
            "target_corpus_id": self.target_corpus_id,
            "owner_tenant_id": self.owner_tenant_id,
            "review_mode": self.review_mode,
            "seed_urls": list(self.seed_urls),
            "sitemap_url": self.sitemap_url,
        }


@dataclass
class ScrapedPage:
    """A single scraped page belonging to a job."""

    job_id: str
    url: str
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    status: PageStatus = PageStatus.QUEUED
    title: str = ""
    word_count: int = 0
    content_md: str = ""
    content_html: str = ""
    rejection_reason: str | None = None
    fetched_at: str | None = None
    extracted_at: str | None = None
    # Why the last delivery attempt failed; cleared once the engine accepts it.
    delivery_error: str | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        """Serialize to dictionary for API responses and DB storage."""
        return {
            "id": self.id,
            "job_id": self.job_id,
            "url": self.url,
            "status": self.status.value,
            "title": self.title,
            "word_count": self.word_count,
            "content_md": self.content_md,
            "content_html": self.content_html,
            "rejection_reason": self.rejection_reason,
            "fetched_at": self.fetched_at,
            "extracted_at": self.extracted_at,
            "delivery_error": self.delivery_error,
        }
