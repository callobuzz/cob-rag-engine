"""API route handlers for the scraper service.

Every route needs an engine credential — a key in ``X-API-Key`` or a dashboard
sign-in in ``Authorization: Bearer`` (see ``scraper.api.auth``) — and only sees the
jobs it may see: an admin all of them, a tenant (key or sign-in) the jobs it
submitted, a corpus key the jobs that target its corpus. A job outside the
caller's scope is reported as 404, exactly like a job that does not exist.
"""

import asyncio
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from scraper.api.auth import Caller, CallerDep, Credential, EngineError, check_corpus_access
from scraper.api.schemas import ApproveRequest, ScrapeRequest, ScrapeResponse
from scraper.config import ScraperSettings
from scraper.crawl.netguard import BlockedURLError, check_url
from scraper.crawl.orchestrator import CrawlOrchestrator
from scraper.deliver.sender import DeliverySender
from scraper.storage.database import Database
from scraper.storage.models import JobStatus, PageStatus, ScrapeJob

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

REVIEW_MODE_REQUIRED = (
    "Review mode is required when you are signed in: "
    "pages are delivered when you press Deliver"
)


async def _visible_job(request: Request, caller: Caller, job_id: str) -> ScrapeJob:
    """Return the job if it exists and the caller may see it, else 404."""
    db: Database = request.app.state.db
    job = await db.get_job(job_id)
    if job is None or not caller.can_see(job):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def approve_all_verified(db: Database, job_id: str) -> int:
    """Move every VERIFIED page of a job to APPROVED (ready for delivery)."""
    pages = await db.list_pages(job_id, status=PageStatus.VERIFIED)
    for page in pages:
        page.status = PageStatus.APPROVED
        await db.update_page(page)
    return len(pages)


async def approve_page_ids(db: Database, job_id: str, page_ids: list[str]) -> int:
    """Move the named pages of a job to APPROVED; only VERIFIED pages qualify."""
    approved = 0
    for page_id in page_ids:
        page = await db.get_page(page_id)
        if page and page.job_id == job_id and page.status == PageStatus.VERIFIED:
            page.status = PageStatus.APPROVED
            await db.update_page(page)
            approved += 1
    return approved


async def deliver_job_pages(
    db: Database,
    settings: ScraperSettings,
    data_dir: str,
    job: ScrapeJob,
    credential: Credential,
) -> list[dict[str, Any]]:
    """Send a job's APPROVED pages to the engine's ingest API as ``credential``.

    The destination is always ``settings.rag_url`` + ``/api/v1/ingest`` and the
    corpus is always the job's own target corpus. A page the engine accepts
    becomes DELIVERED; a page that fails stays APPROVED with ``delivery_error``
    set, so calling this again re-sends only what failed.

    An engine 401 (an expired sign-in, a rotated key) is the caller's problem, not
    the page's: delivery stops, the page is left untouched and the 401 is raised
    with the engine's code, so the dashboard refreshes its token and retries.
    """
    sender = DeliverySender(
        rag_url=settings.rag_url, data_dir=data_dir, rate_limit=settings.delivery_rate
    )
    pages = await db.list_pages(job.id, status=PageStatus.APPROVED)
    results: list[dict[str, Any]] = []
    for page in pages:
        result = await sender.deliver_page(
            job_id=job.id,
            page_id=page.id,
            title=page.title,
            auth_headers=credential.headers(),
            corpus_id=job.target_corpus_id,
        )
        if result.status_code == 401:
            raise EngineError(
                401, result.detail or "Credential not accepted", result.code, result.extra
            )
        if result.success:
            page.status = PageStatus.DELIVERED
            page.delivery_error = None
        else:
            page.delivery_error = result.error or "Delivery failed"
        await db.update_page(page)
        entry: dict[str, Any] = {"page_id": result.page_id, "success": result.success}
        if result.error:
            entry["error"] = result.error
        results.append(entry)
    return results


def _delivery_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    delivered = sum(1 for r in results if r["success"])
    return {
        "attempted": len(results),
        "delivered": delivered,
        "failed": len(results) - delivered,
        "results": results,
    }


async def run_job(
    orchestrator: CrawlOrchestrator,
    db: Database,
    settings: ScraperSettings,
    data_dir: str,
    job: ScrapeJob,
    credential: Credential,
) -> None:
    """Crawl a job and, when review mode is off, deliver its verified pages.

    ``credential`` is the submitting request's key (review mode off is refused for
    a sign-in, whose token would expire mid-job). It is held only in this task's
    memory — never stored — so if the service restarts before the crawl ends the
    key is gone and the verified pages wait for a manual approve + deliver. Pages
    that fail delivery stay APPROVED with their error, for a manual retry.
    """
    try:
        await orchestrator.crawl(job)
        if job.review_mode:
            return
        await approve_all_verified(db, job.id)
        results = await deliver_job_pages(db, settings, data_dir, job, credential)
        failed = _delivery_summary(results)["failed"]
        job.status = JobStatus.COMPLETED
        job.error = f"{failed} of {len(results)} pages failed delivery" if failed else None
        job.updated_at = datetime.now(UTC).isoformat()
        await db.update_job(job)
    except Exception:
        logger.exception("Scrape job %s failed", job.id)
        job.status = JobStatus.FAILED
        job.error = "Crawl or delivery failed; see scraper logs"
        job.updated_at = datetime.now(UTC).isoformat()
        try:
            await db.update_job(job)
        except Exception:
            logger.exception("Could not record failure of job %s", job.id)


async def start_crawl_background(request: Request, job: ScrapeJob, caller: Caller) -> None:
    """Create a CrawlOrchestrator and dispatch the crawl as a background task."""
    settings: ScraperSettings = request.app.state.settings
    db: Database = request.app.state.db
    data_dir: str = request.app.state.data_dir
    orchestrator = CrawlOrchestrator(
        db=db,
        data_dir=data_dir,
        concurrency=settings.concurrency,
        httpx_timeout=settings.httpx_timeout,
        playwright_timeout=settings.playwright_timeout,
        min_word_count=settings.min_word_count,
        allow_private_networks=settings.allow_private_networks,
        max_response_bytes=settings.max_response_bytes,
    )
    task = asyncio.create_task(
        run_job(orchestrator, db, settings, data_dir, job, caller.credential)
    )
    # Keep a strong reference until the task ends, or it may be garbage-collected.
    tasks: set[asyncio.Task[None]] = request.app.state.background_tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)


@router.post("/scrape", status_code=202, response_model=ScrapeResponse)
async def submit_scrape(body: ScrapeRequest, request: Request, caller: CallerDep) -> ScrapeResponse:
    """Create a scrape job for a corpus the caller may use and start crawling."""
    if caller.is_session and not body.review_mode:
        # Background delivery would outlive a 15-minute access token.
        raise HTTPException(status_code=422, detail=REVIEW_MODE_REQUIRED)
    if caller.kind == "corpus":
        if body.target_corpus_id and body.target_corpus_id != caller.corpus_id:
            raise HTTPException(
                status_code=403, detail="A corpus key can only scrape into its own corpus"
            )
        target_corpus_id = str(caller.corpus_id)
    else:
        if not body.target_corpus_id:
            raise HTTPException(
                status_code=422,
                detail="target_corpus_id is required with a tenant or admin key",
            )
        target_corpus_id = body.target_corpus_id
        await check_corpus_access(request, caller, target_corpus_id)

    settings: ScraperSettings = request.app.state.settings
    seeds = list(dict.fromkeys(u.strip() for u in body.urls or [] if u.strip()))
    sitemap_url = body.sitemap_url.strip() if body.sitemap_url else None
    if not seeds and not sitemap_url:
        raise HTTPException(status_code=422, detail="No usable URL or sitemap_url given")
    for url in [*seeds, *([sitemap_url] if sitemap_url else [])]:
        try:
            await check_url(url, settings.allow_private_networks)
        except BlockedURLError as exc:
            raise HTTPException(status_code=422, detail=f"{url}: {exc}") from exc

    # job.url is the display root: the first seed, else the sitemap.
    job = ScrapeJob(
        url=seeds[0] if seeds else str(sitemap_url),
        target_corpus_id=target_corpus_id,
        owner_tenant_id=None if caller.is_admin else caller.tenant_id,
        review_mode=body.review_mode,
        seed_urls=seeds,
        sitemap_url=sitemap_url,
    )
    if body.max_pages is not None:
        job.max_pages = body.max_pages

    db: Database = request.app.state.db
    await db.create_job(job)
    await start_crawl_background(request, job, caller)
    return ScrapeResponse(job_id=job.id, status=job.status.value)


@router.get("/jobs")
async def list_jobs(request: Request, caller: CallerDep) -> list[dict[str, Any]]:
    """List the scrape jobs the caller may see."""
    db: Database = request.app.state.db
    jobs = await db.list_jobs()
    return [j.to_dict() for j in jobs if caller.can_see(j)]


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    """Get job detail with page counts."""
    job = await _visible_job(request, caller, job_id)
    db: Database = request.app.state.db
    result: dict[str, Any] = job.to_dict()
    result["page_counts"] = await db.count_pages_by_status(job_id)
    return result


@router.get("/jobs/{job_id}/pages")
async def list_pages(
    job_id: str, request: Request, caller: CallerDep, status: str | None = None
) -> list[dict[str, Any]]:
    """List pages for a job, optionally filtered by status."""
    await _visible_job(request, caller, job_id)
    try:
        page_status = PageStatus(status) if status else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown page status: {status}") from exc
    db: Database = request.app.state.db
    pages = await db.list_pages(job_id, status=page_status)
    return [p.to_dict() for p in pages]


@router.get("/jobs/{job_id}/pages/{page_id}")
async def get_page(
    job_id: str, page_id: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Get page detail with content from filesystem."""
    await _visible_job(request, caller, job_id)
    db: Database = request.app.state.db
    page = await db.get_page(page_id)
    if page is None or page.job_id != job_id:
        raise HTTPException(status_code=404, detail="Page not found")

    result: dict[str, Any] = page.to_dict()
    file_path = Path(request.app.state.data_dir) / "jobs" / job_id / f"{page_id}.md"
    if file_path.exists():
        result["content_file"] = file_path.read_text(encoding="utf-8")
    return result


@router.post("/jobs/{job_id}/approve")
async def approve_pages(
    job_id: str, body: ApproveRequest, request: Request, caller: CallerDep
) -> dict[str, int]:
    """Approve pages — all verified or specific IDs."""
    await _visible_job(request, caller, job_id)
    db: Database = request.app.state.db

    approved_count = 0
    if body.approve_all_verified:
        approved_count = await approve_all_verified(db, job_id)
    elif body.page_ids:
        approved_count = await approve_page_ids(db, job_id, body.page_ids)
    return {"approved": approved_count}


@router.post("/jobs/{job_id}/deliver")
async def deliver_pages(job_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    """Deliver APPROVED pages to the engine's ingest API, as the caller.

    Uses the caller's own credential, so the engine enforces the caller's access
    to the job's corpus a second time.
    """
    job = await _visible_job(request, caller, job_id)
    if not job.target_corpus_id:
        raise HTTPException(
            status_code=409,
            detail="Job has no target corpus (created before corpus targeting); re-submit it",
        )
    results = await deliver_job_pages(
        request.app.state.db,
        request.app.state.settings,
        request.app.state.data_dir,
        job,
        caller.credential,
    )
    return _delivery_summary(results)


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str, request: Request, caller: CallerDep) -> dict[str, bool]:
    """Delete a job and its staged content."""
    await _visible_job(request, caller, job_id)

    job_dir = Path(request.app.state.data_dir) / "jobs" / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir)

    db: Database = request.app.state.db
    await db.delete_job(job_id)
    return {"deleted": True}
