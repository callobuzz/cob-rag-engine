"""scraper/crawl/orchestrator.py — Crawl orchestrator: resolve URLs, fetch, extract, verify."""

import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import httpx

from scraper.crawl.fetcher import FetchResult, PageFetcher
from scraper.crawl.netguard import (
    DEFAULT_MAX_RESPONSE_BYTES,
    BlockedURLError,
    ResponseTooLargeError,
    guarded_get,
    make_client,
)
from scraper.crawl.sitemap import parse_sitemap_index, parse_sitemap_xml
from scraper.extract.extractor import ExtractionResult, extract_content
from scraper.storage.database import Database
from scraper.storage.models import JobStatus, PageStatus, ScrapedPage, ScrapeJob
from scraper.verify.rules import verify_page

logger = logging.getLogger(__name__)

# A sitemap index may point at more indexes; stop following them after this depth.
MAX_SITEMAP_DEPTH = 3


class CrawlOrchestrator:
    """Orchestrate crawling: resolve URLs, fetch pages, extract, verify, save."""

    def __init__(
        self,
        db: Database,
        data_dir: str,
        concurrency: int = 5,
        httpx_timeout: int = 30,
        playwright_timeout: int = 60,
        min_word_count: int = 50,
        allow_private_networks: bool = False,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self.db = db
        self.data_dir = data_dir
        self.concurrency = concurrency
        self.allow_private = allow_private_networks
        self.max_response_bytes = max_response_bytes
        self.fetcher = PageFetcher(
            httpx_timeout=httpx_timeout,
            playwright_timeout=playwright_timeout,
            allow_private_networks=allow_private_networks,
            max_response_bytes=max_response_bytes,
        )
        self.min_word_count = min_word_count

    async def crawl(self, job: ScrapeJob) -> None:
        """Run the full crawl pipeline for a job."""
        job.status = JobStatus.CRAWLING
        await self.db.update_job(job)

        urls = await self._resolve_urls(job)
        # Limit to max_pages
        urls = urls[: job.max_pages]
        job.pages_found = len(urls)
        await self.db.update_job(job)

        content_hashes: set[str] = set()
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _bounded_crawl(url: str) -> None:
            async with semaphore:
                await self._crawl_page(job.id, url, content_hashes)

        tasks = [_bounded_crawl(url) for url in urls]
        await asyncio.gather(*tasks)

        # Update job status to reviewing
        job.status = JobStatus.VERIFYING
        job.pages_done = len(urls)
        job.updated_at = datetime.now(UTC).isoformat()
        await self.db.update_job(job)

    async def _resolve_urls(self, job: ScrapeJob) -> list[str]:
        """Every seed URL, then the sitemap's URLs — deduplicated, at most max_pages.

        Jobs created before seeds were stored have neither; for those the root URL
        and ``{root}/sitemap.xml`` are used, as before.
        """
        urls: list[str] = list(job.seed_urls)
        sitemap_url = job.sitemap_url
        if not urls and not sitemap_url:
            urls = [job.url]
            sitemap_url = job.url.rstrip("/") + "/sitemap.xml"

        if sitemap_url and len(urls) < job.max_pages:
            urls.extend(await self._fetch_sitemap(sitemap_url, limit=job.max_pages))

        seen: set[str] = set()
        unique: list[str] = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                unique.append(url)
        return unique[: job.max_pages]

    async def _fetch_sitemap(self, url: str, limit: int, depth: int = 0) -> list[str]:
        """Fetch and parse a sitemap XML, following a sitemap index up to a depth."""
        try:
            async with make_client(self.allow_private, 15) as client:
                response = await guarded_get(
                    client,
                    url,
                    self.allow_private,
                    max_bytes=self.max_response_bytes,
                    total_timeout=15,
                )
                if response.status_code >= 400:
                    return []
                xml_content = response.text
        except (BlockedURLError, ResponseTooLargeError) as exc:
            logger.warning("Sitemap %s skipped: %s", url, exc)
            return []
        except httpx.HTTPError:
            return []

        try:
            index_urls = parse_sitemap_index(xml_content)
            if index_urls is None:
                return parse_sitemap_xml(xml_content)[:limit]
        except ET.ParseError:
            logger.warning("Sitemap %s is not valid XML", url)
            return []

        if depth >= MAX_SITEMAP_DEPTH:
            return []
        all_urls: list[str] = []
        for sub_url in index_urls:
            if len(all_urls) >= limit:
                break
            all_urls.extend(
                await self._fetch_sitemap(sub_url, limit - len(all_urls), depth + 1)
            )
        return all_urls[:limit]

    async def _crawl_page(
        self,
        job_id: str,
        url: str,
        content_hashes: set[str],
    ) -> None:
        """Fetch, extract, verify, and save a single page."""
        page = ScrapedPage(job_id=job_id, url=url)
        await self.db.create_page(page)

        result = await self._fetch_and_extract(url)
        if result is None:
            page.status = PageStatus.FAILED
            page.rejection_reason = "Fetch failed"
            await self.db.update_page(page)
            return

        fetch_result, extraction = result
        page.status = PageStatus.EXTRACTED
        page.title = extraction.title
        page.word_count = extraction.word_count
        page.content_md = extraction.content
        page.content_html = fetch_result.html
        page.fetched_at = datetime.now(UTC).isoformat()
        page.extracted_at = datetime.now(UTC).isoformat()

        # Verify
        verification = verify_page(
            content=extraction.content,
            word_count=extraction.word_count,
            content_hash=extraction.content_hash,
            existing_hashes=content_hashes,
            min_word_count=self.min_word_count,
        )

        if verification.status == "verified":
            page.status = PageStatus.VERIFIED
            content_hashes.add(extraction.content_hash)
            self._save_content(job_id, page.id, extraction.content)
        elif verification.status == "flagged":
            page.status = PageStatus.REJECTED
            page.rejection_reason = "; ".join(verification.flag_reasons)
        else:
            page.status = PageStatus.FAILED
            page.rejection_reason = "; ".join(verification.flag_reasons)

        await self.db.update_page(page)

    async def _fetch_and_extract(
        self, url: str
    ) -> tuple[FetchResult, ExtractionResult] | None:
        """Fetch a URL and extract content. Returns None on failure."""
        fetch_result = await self.fetcher.fetch(url)
        if fetch_result.error:
            return None

        extraction = extract_content(fetch_result.html, url)
        return fetch_result, extraction

    def _save_content(self, job_id: str, page_id: str, content: str) -> None:
        """Write extracted markdown content to the filesystem."""
        job_dir = Path(self.data_dir) / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        file_path = job_dir / f"{page_id}.md"
        file_path.write_text(content, encoding="utf-8")
