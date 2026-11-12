"""Async SQLite database layer for scraper storage."""

import json
from typing import Any

import aiosqlite

from scraper.storage.models import (
    JobStatus,
    PageStatus,
    ScrapedPage,
    ScrapeJob,
)

JOBS_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    max_pages INTEGER NOT NULL DEFAULT 50,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    pages_found INTEGER NOT NULL DEFAULT 0,
    pages_done INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    target_corpus_id TEXT NOT NULL DEFAULT '',
    owner_tenant_id TEXT NULL,
    review_mode INTEGER NOT NULL DEFAULT 1,
    seed_urls TEXT NOT NULL DEFAULT '[]',
    sitemap_url TEXT NULL
)
"""

# Columns added after the first release. initialize() adds any that an existing
# SQLite file lacks, so old volumes keep working (their jobs get the defaults).
JOBS_ADDED_COLUMNS: dict[str, str] = {
    "target_corpus_id": "TEXT NOT NULL DEFAULT ''",
    "owner_tenant_id": "TEXT NULL",
    "review_mode": "INTEGER NOT NULL DEFAULT 1",
    "seed_urls": "TEXT NOT NULL DEFAULT '[]'",
    "sitemap_url": "TEXT NULL",
}
PAGES_ADDED_COLUMNS: dict[str, str] = {
    "delivery_error": "TEXT NULL",
}

PAGES_TABLE = """
CREATE TABLE IF NOT EXISTS pages (
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
    extracted_at TEXT,
    delivery_error TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(id)
)
"""


class Database:
    """Async SQLite wrapper for scraper persistence."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Open connection and create tables if needed."""
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(JOBS_TABLE)
        await self._db.execute(PAGES_TABLE)
        await self._add_missing_columns("jobs", JOBS_ADDED_COLUMNS)
        await self._add_missing_columns("pages", PAGES_ADDED_COLUMNS)
        await self._db.commit()

    async def _add_missing_columns(self, table: str, columns: dict[str, str]) -> None:
        """ALTER TABLE ... ADD COLUMN for every column an older file lacks."""
        cursor = await self.db.execute(f"PRAGMA table_info({table})")
        existing = {row["name"] for row in await cursor.fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                # Table, names and DDL are the constants above, never user input.
                await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    @staticmethod
    def _job_params(job: ScrapeJob) -> dict[str, Any]:
        d = job.to_dict()
        d["seed_urls"] = json.dumps(job.seed_urls)
        d["review_mode"] = int(job.review_mode)
        return d

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        """Return the active connection or raise."""
        if self._db is None:
            raise RuntimeError("Database not initialized — call initialize() first")
        return self._db

    # ── Jobs ──────────────────────────────────────────────────────────

    async def create_job(self, job: ScrapeJob) -> ScrapeJob:
        """Insert a new job row."""
        d = self._job_params(job)
        await self.db.execute(
            """INSERT INTO jobs (id, url, max_pages, status, created_at, updated_at,
               pages_found, pages_done, error, target_corpus_id, owner_tenant_id,
               review_mode, seed_urls, sitemap_url)
               VALUES (:id, :url, :max_pages, :status, :created_at, :updated_at,
               :pages_found, :pages_done, :error, :target_corpus_id, :owner_tenant_id,
               :review_mode, :seed_urls, :sitemap_url)""",
            d,
        )
        await self.db.commit()
        return job

    async def get_job(self, job_id: str) -> ScrapeJob | None:
        """Fetch a single job by ID."""
        cursor = await self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    async def list_jobs(self) -> list[ScrapeJob]:
        """Return all jobs ordered by creation time (newest first)."""
        cursor = await self.db.execute("SELECT * FROM jobs ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [self._row_to_job(r) for r in rows]

    async def update_job(self, job: ScrapeJob) -> None:
        """Update an existing job row."""
        d = self._job_params(job)
        await self.db.execute(
            """UPDATE jobs SET url=:url, max_pages=:max_pages, status=:status,
               updated_at=:updated_at, pages_found=:pages_found,
               pages_done=:pages_done, error=:error,
               target_corpus_id=:target_corpus_id, owner_tenant_id=:owner_tenant_id,
               review_mode=:review_mode, seed_urls=:seed_urls, sitemap_url=:sitemap_url
               WHERE id=:id""",
            d,
        )
        await self.db.commit()

    async def delete_job(self, job_id: str) -> None:
        """Delete a job and its associated pages."""
        await self.db.execute("DELETE FROM pages WHERE job_id = ?", (job_id,))
        await self.db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        await self.db.commit()

    # ── Pages ─────────────────────────────────────────────────────────

    async def create_page(self, page: ScrapedPage) -> ScrapedPage:
        """Insert a new page row."""
        d = page.to_dict()
        await self.db.execute(
            """INSERT INTO pages (id, job_id, url, status, title, word_count,
               content_md, content_html, rejection_reason, fetched_at, extracted_at,
               delivery_error)
               VALUES (:id, :job_id, :url, :status, :title, :word_count,
               :content_md, :content_html, :rejection_reason, :fetched_at, :extracted_at,
               :delivery_error)""",
            d,
        )
        await self.db.commit()
        return page

    async def get_page(self, page_id: str) -> ScrapedPage | None:
        """Fetch a single page by ID."""
        cursor = await self.db.execute("SELECT * FROM pages WHERE id = ?", (page_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_page(row)

    async def list_pages(
        self, job_id: str, status: PageStatus | None = None
    ) -> list[ScrapedPage]:
        """Return pages for a job, optionally filtered by status."""
        if status is not None:
            cursor = await self.db.execute(
                "SELECT * FROM pages WHERE job_id = ? AND status = ?",
                (job_id, status.value),
            )
        else:
            cursor = await self.db.execute(
                "SELECT * FROM pages WHERE job_id = ?", (job_id,)
            )
        rows = await cursor.fetchall()
        return [self._row_to_page(r) for r in rows]

    async def update_page(self, page: ScrapedPage) -> None:
        """Update an existing page row."""
        d = page.to_dict()
        await self.db.execute(
            """UPDATE pages SET status=:status, title=:title, word_count=:word_count,
               content_md=:content_md, content_html=:content_html,
               rejection_reason=:rejection_reason, fetched_at=:fetched_at,
               extracted_at=:extracted_at, delivery_error=:delivery_error
               WHERE id=:id""",
            d,
        )
        await self.db.commit()

    async def count_pages_by_status(self, job_id: str) -> dict[str, int]:
        """Return a {status: count} mapping for a job's pages."""
        cursor = await self.db.execute(
            "SELECT status, COUNT(*) as cnt FROM pages WHERE job_id = ? GROUP BY status",
            (job_id,),
        )
        rows = await cursor.fetchall()
        return {row["status"]: row["cnt"] for row in rows}

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _row_to_job(row: aiosqlite.Row) -> ScrapeJob:
        """Convert a database row to a ScrapeJob instance."""
        return ScrapeJob(
            id=row["id"],
            url=row["url"],
            max_pages=row["max_pages"],
            status=JobStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            pages_found=row["pages_found"],
            pages_done=row["pages_done"],
            error=row["error"],
            target_corpus_id=row["target_corpus_id"] or "",
            owner_tenant_id=row["owner_tenant_id"],
            review_mode=bool(row["review_mode"]),
            seed_urls=_load_urls(row["seed_urls"]),
            sitemap_url=row["sitemap_url"],
        )

    @staticmethod
    def _row_to_page(row: aiosqlite.Row) -> ScrapedPage:
        """Convert a database row to a ScrapedPage instance."""
        return ScrapedPage(
            id=row["id"],
            job_id=row["job_id"],
            url=row["url"],
            status=PageStatus(row["status"]),
            title=row["title"],
            word_count=row["word_count"],
            content_md=row["content_md"],
            content_html=row["content_html"],
            rejection_reason=row["rejection_reason"],
            fetched_at=row["fetched_at"],
            extracted_at=row["extracted_at"],
            delivery_error=row["delivery_error"],
        )


def _load_urls(raw: str | None) -> list[str]:
    """Decode the stored seed-URL list; anything unreadable counts as none."""
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return [str(u) for u in value] if isinstance(value, list) else []
