"""Tests for the scraper FastAPI application and API endpoints."""

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pytest_httpx import HTTPXMock

from scraper.api.app import create_app
from scraper.config import ScraperSettings
from scraper.crawl.orchestrator import CrawlOrchestrator
from scraper.storage.database import Database
from scraper.storage.models import JobStatus, PageStatus, ScrapedPage, ScrapeJob
from tests.conftest import (
    ADMIN_KEY,
    CK_A1,
    CK_B1,
    CORPUS_A1,
    CORPUS_A2,
    CORPUS_B1,
    RAG_URL,
    TENANT_A,
    TENANT_B,
    TK_A,
    TK_B,
    FakeEngine,
    auth,
    ingest_form,
)


@pytest.fixture
def no_crawl():
    with patch("scraper.api.routes.start_crawl_background", new_callable=AsyncMock) as m:
        yield m


async def _submit(client: AsyncClient, key: str, **body: object):
    payload: dict[str, object] = {"urls": ["https://example.com"]}
    payload.update(body)
    return await client.post("/api/v1/scrape", json=payload, headers=auth(key))


async def _make_job(
    db: Database, corpus_id: str, tenant_id: str | None, review_mode: bool = True
) -> ScrapeJob:
    job = ScrapeJob(
        url="https://site.example.com",
        target_corpus_id=corpus_id,
        owner_tenant_id=tenant_id,
        review_mode=review_mode,
    )
    await db.create_job(job)
    return job


# ── Auth ─────────────────────────────────────────────────────────────


async def test_missing_key_is_401(client: AsyncClient, engine: FakeEngine) -> None:
    resp = await client.get("/api/v1/jobs")
    assert resp.status_code == 401
    assert engine.whoami_calls == 0


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/scrape"),
        ("GET", "/api/v1/jobs/abc"),
        ("GET", "/api/v1/jobs/abc/pages"),
        ("GET", "/api/v1/jobs/abc/pages/p1"),
        ("POST", "/api/v1/jobs/abc/approve"),
        ("POST", "/api/v1/jobs/abc/deliver"),
        ("DELETE", "/api/v1/jobs/abc"),
    ],
)
async def test_every_route_requires_a_key(client: AsyncClient, method: str, path: str) -> None:
    resp = await client.request(method, path)
    assert resp.status_code == 401


async def test_bad_key_is_401(client: AsyncClient, engine: FakeEngine) -> None:
    resp = await client.get("/api/v1/jobs", headers=auth("ck_wrong"))
    assert resp.status_code == 401
    assert engine.whoami_calls == 1


async def test_engine_unreachable_is_503(
    db: Database, data_dir: Path, settings: ScraperSettings, httpx_mock: HTTPXMock
) -> None:
    import httpx

    httpx_mock.add_exception(httpx.ConnectError("refused"))
    app = create_app(db=db, data_dir=str(data_dir), settings=settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/v1/jobs", headers=auth(ADMIN_KEY))
    assert resp.status_code == 503


async def test_identity_is_cached(client: AsyncClient, engine: FakeEngine) -> None:
    for _ in range(3):
        assert (await client.get("/api/v1/jobs", headers=auth(TK_A))).status_code == 200
    assert engine.whoami_calls == 1


def test_cors_is_limited_to_configured_origins(settings: ScraperSettings) -> None:
    app = create_app(db=None, data_dir="x", settings=settings)
    cors = [m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"]
    assert cors and cors[0].kwargs["allow_origins"] == settings.cors_origins
    assert "*" not in settings.cors_origins
    assert cors[0].kwargs["allow_headers"] == ["Content-Type", "X-API-Key", "Authorization"]


# ── Submit ───────────────────────────────────────────────────────────


async def test_corpus_key_defaults_to_its_own_corpus(
    client: AsyncClient, db: Database, no_crawl: AsyncMock
) -> None:
    resp = await _submit(client, CK_A1)
    assert resp.status_code == 202
    job = await db.get_job(resp.json()["job_id"])
    assert job is not None
    assert job.target_corpus_id == CORPUS_A1
    assert job.owner_tenant_id == TENANT_A
    assert job.review_mode is True
    no_crawl.assert_awaited_once()


async def test_corpus_key_may_name_its_own_corpus(
    client: AsyncClient, no_crawl: AsyncMock
) -> None:
    resp = await _submit(client, CK_A1, target_corpus_id=CORPUS_A1)
    assert resp.status_code == 202


async def test_corpus_key_refused_for_another_corpus(
    client: AsyncClient, db: Database, no_crawl: AsyncMock
) -> None:
    resp = await _submit(client, CK_A1, target_corpus_id=CORPUS_A2)
    assert resp.status_code == 403
    assert await db.list_jobs() == []
    no_crawl.assert_not_awaited()


async def test_tenant_key_may_use_its_corpus(
    client: AsyncClient, db: Database, no_crawl: AsyncMock
) -> None:
    resp = await _submit(client, TK_A, target_corpus_id=CORPUS_A2, review_mode=False, max_pages=7)
    assert resp.status_code == 202
    job = await db.get_job(resp.json()["job_id"])
    assert job is not None
    assert (job.target_corpus_id, job.owner_tenant_id) == (CORPUS_A2, TENANT_A)
    assert job.review_mode is False
    assert job.max_pages == 7


async def test_tenant_key_refused_for_corpus_engine_rejects(
    client: AsyncClient, db: Database, no_crawl: AsyncMock
) -> None:
    resp = await _submit(client, TK_A, target_corpus_id=CORPUS_B1)
    assert resp.status_code == 403
    assert await db.list_jobs() == []


async def test_tenant_key_requires_target_corpus(
    client: AsyncClient, no_crawl: AsyncMock
) -> None:
    resp = await _submit(client, TK_A)
    assert resp.status_code == 422


async def test_admin_job_has_no_owner_tenant(
    client: AsyncClient, db: Database, no_crawl: AsyncMock
) -> None:
    resp = await _submit(client, ADMIN_KEY, target_corpus_id=CORPUS_B1)
    assert resp.status_code == 202
    job = await db.get_job(resp.json()["job_id"])
    assert job is not None
    assert (job.target_corpus_id, job.owner_tenant_id) == (CORPUS_B1, None)


async def test_admin_unknown_corpus_is_404(client: AsyncClient, no_crawl: AsyncMock) -> None:
    resp = await _submit(client, ADMIN_KEY, target_corpus_id=str(uuid.uuid4()))
    assert resp.status_code == 404


async def test_non_uuid_corpus_is_422(client: AsyncClient, no_crawl: AsyncMock) -> None:
    resp = await _submit(client, ADMIN_KEY, target_corpus_id="../tenants")
    assert resp.status_code == 422


async def test_old_target_fields_are_ignored(
    client: AsyncClient, db: Database, no_crawl: AsyncMock
) -> None:
    """An old client sending target_url/target_api_key is not rejected, and they are unused."""
    resp = await _submit(
        client,
        CK_A1,
        target_url="https://evil.example.com/collect",
        target_api_key="leak",
    )
    assert resp.status_code == 202
    job = await db.get_job(resp.json()["job_id"])
    assert job is not None
    assert "evil" not in str(job.to_dict())


async def test_submit_stores_every_seed_and_sitemap(
    client: AsyncClient, db: Database, no_crawl: AsyncMock
) -> None:
    resp = await _submit(
        client,
        CK_A1,
        urls=["https://a.example.com/1", "https://a.example.com/2", "https://a.example.com/1"],
        sitemap_url="https://a.example.com/sitemap.xml",
    )
    assert resp.status_code == 202
    job = await db.get_job(resp.json()["job_id"])
    assert job is not None
    assert job.url == "https://a.example.com/1"
    assert job.seed_urls == ["https://a.example.com/1", "https://a.example.com/2"]
    assert job.sitemap_url == "https://a.example.com/sitemap.xml"


async def test_sitemap_only_job_uses_sitemap_as_root(
    client: AsyncClient, db: Database, no_crawl: AsyncMock
) -> None:
    resp = await client.post(
        "/api/v1/scrape",
        json={"sitemap_url": "https://a.example.com/sitemap.xml"},
        headers=auth(CK_A1),
    )
    job = await db.get_job(resp.json()["job_id"])
    assert job is not None
    assert (job.url, job.seed_urls) == ("https://a.example.com/sitemap.xml", [])


@pytest.mark.parametrize(
    "bad",
    [
        {"urls": ["http://127.0.0.1/"]},
        {"urls": ["https://ok.example.com", "http://10.1.2.3/admin"]},
        {"urls": ["http://169.254.169.254/latest/meta-data/"]},
        {"urls": ["http://[::1]:8000/"]},
        {"urls": ["http://app:8000/api/v1/corpora"]},
        {"urls": ["file:///etc/passwd"]},
        {"sitemap_url": "http://localhost/sitemap.xml"},
    ],
)
async def test_submit_refuses_private_targets(
    client: AsyncClient, db: Database, no_crawl: AsyncMock, bad: dict[str, object]
) -> None:
    resp = await client.post("/api/v1/scrape", json=bad, headers=auth(CK_A1))
    assert resp.status_code == 422
    assert await db.list_jobs() == []
    no_crawl.assert_not_awaited()


async def test_submit_refuses_public_name_resolving_private(
    client: AsyncClient, fake_dns: dict[str, list[str]], no_crawl: AsyncMock
) -> None:
    fake_dns["sneaky.example.com"] = ["10.0.0.5"]
    resp = await _submit(client, CK_A1, urls=["https://sneaky.example.com/"])
    assert resp.status_code == 422


async def test_submit_allows_private_when_configured(
    db: Database,
    data_dir: Path,
    settings: ScraperSettings,
    engine: FakeEngine,
    no_crawl: AsyncMock,
) -> None:
    open_settings = settings.model_copy(update={"allow_private_networks": True})
    app = create_app(db=db, data_dir=str(data_dir), settings=open_settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await _submit(c, CK_A1, urls=["http://10.1.2.3/intranet"])
    assert resp.status_code == 202


async def test_submit_requires_url_or_sitemap(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/scrape", json={"target_corpus_id": CORPUS_A1}, headers=auth(CK_A1)
    )
    assert resp.status_code == 422


# ── Visibility ───────────────────────────────────────────────────────


async def test_list_jobs_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/jobs", headers=auth(ADMIN_KEY))
    assert resp.status_code == 200
    assert resp.json() == []


async def test_visibility_across_tenants(client: AsyncClient, db: Database) -> None:
    job_a = await _make_job(db, CORPUS_A1, TENANT_A)
    job_a2 = await _make_job(db, CORPUS_A2, TENANT_A)
    job_b = await _make_job(db, CORPUS_B1, TENANT_B)
    legacy = ScrapeJob(url="https://old.example.com")
    await db.create_job(legacy)

    async def ids(key: str) -> set[str]:
        resp = await client.get("/api/v1/jobs", headers=auth(key))
        assert resp.status_code == 200
        return {j["id"] for j in resp.json()}

    assert await ids(TK_A) == {job_a.id, job_a2.id}
    assert await ids(TK_B) == {job_b.id}
    assert await ids(CK_A1) == {job_a.id}
    assert await ids(CK_B1) == {job_b.id}
    assert await ids(ADMIN_KEY) == {job_a.id, job_a2.id, job_b.id, legacy.id}

    page = ScrapedPage(job_id=job_a.id, url="https://site.example.com/p", id="pageA")
    await db.create_page(page)

    for key in (TK_B, CK_B1):
        h = auth(key)
        assert (await client.get(f"/api/v1/jobs/{job_a.id}", headers=h)).status_code == 404
        assert (await client.get(f"/api/v1/jobs/{job_a.id}/pages", headers=h)).status_code == 404
        resp = await client.get(f"/api/v1/jobs/{job_a.id}/pages/pageA", headers=h)
        assert resp.status_code == 404
        resp = await client.post(
            f"/api/v1/jobs/{job_a.id}/approve", json={"approve_all_verified": True}, headers=h
        )
        assert resp.status_code == 404
        assert (
            await client.post(f"/api/v1/jobs/{job_a.id}/deliver", headers=h)
        ).status_code == 404
        assert (await client.delete(f"/api/v1/jobs/{job_a.id}", headers=h)).status_code == 404

    # A corpus key of the same tenant cannot see a sibling corpus's job.
    resp = await client.get(f"/api/v1/jobs/{job_a2.id}", headers=auth(CK_A1))
    assert resp.status_code == 404
    # Legacy jobs are admin-only.
    legacy_path = f"/api/v1/jobs/{legacy.id}"
    assert (await client.get(legacy_path, headers=auth(TK_A))).status_code == 404
    assert (await client.get(legacy_path, headers=auth(ADMIN_KEY))).status_code == 200

    assert await db.get_job(job_a.id) is not None
    assert (await client.get(f"/api/v1/jobs/{job_a.id}", headers=auth(TK_A))).status_code == 200


async def test_get_job_not_found(client: AsyncClient) -> None:
    resp = await client.get(f"/api/v1/jobs/{uuid.uuid4().hex[:12]}", headers=auth(ADMIN_KEY))
    assert resp.status_code == 404


async def test_unknown_page_status_is_422(client: AsyncClient, db: Database) -> None:
    job = await _make_job(db, CORPUS_A1, TENANT_A)
    resp = await client.get(f"/api/v1/jobs/{job.id}/pages?status=bogus", headers=auth(TK_A))
    assert resp.status_code == 422


async def test_create_get_and_delete_job(client: AsyncClient, no_crawl: AsyncMock) -> None:
    job_id = (await _submit(client, CK_A1)).json()["job_id"]
    get_resp = await client.get(f"/api/v1/jobs/{job_id}", headers=auth(CK_A1))
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == job_id
    assert get_resp.json()["target_corpus_id"] == CORPUS_A1

    del_resp = await client.delete(f"/api/v1/jobs/{job_id}", headers=auth(CK_A1))
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True
    assert (await client.get(f"/api/v1/jobs/{job_id}", headers=auth(CK_A1))).status_code == 404


async def test_approve_all_verified(client: AsyncClient, db: Database) -> None:
    job = await _make_job(db, CORPUS_A1, TENANT_A)
    page = ScrapedPage(
        job_id=job.id, url="https://example.com/page1", status=PageStatus.VERIFIED, title="T"
    )
    await db.create_page(page)

    resp = await client.post(
        f"/api/v1/jobs/{job.id}/approve", json={"approve_all_verified": True}, headers=auth(TK_A)
    )
    assert resp.status_code == 200
    assert resp.json()["approved"] == 1
    updated = await db.get_page(page.id)
    assert updated is not None
    assert updated.status == PageStatus.APPROVED


# ── Deliver ──────────────────────────────────────────────────────────


def _stage_page(data_dir: Path, job_id: str, page_id: str, text: str) -> None:
    job_dir = data_dir / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / f"{page_id}.md").write_text(text, encoding="utf-8")


async def test_deliver_goes_to_engine_with_caller_key_and_job_corpus(
    client: AsyncClient, db: Database, data_dir: Path, engine: FakeEngine
) -> None:
    job = await _make_job(db, CORPUS_A2, TENANT_A)
    page = ScrapedPage(
        job_id=job.id, url=f"{job.url}/about", status=PageStatus.APPROVED, title="About"
    )
    await db.create_page(page)
    _stage_page(data_dir, job.id, page.id, "# About us")

    resp = await client.post(f"/api/v1/jobs/{job.id}/deliver", headers=auth(TK_A))
    assert resp.status_code == 200
    assert resp.json()["delivered"] == 1
    assert resp.json()["results"][0]["success"] is True
    delivered = await db.get_page(page.id)
    assert delivered is not None
    assert delivered.status == PageStatus.DELIVERED
    assert delivered.delivery_error is None

    assert len(engine.ingests) == 1
    sent = engine.ingests[0]
    assert str(sent.url) == f"{RAG_URL}/api/v1/ingest"
    assert sent.headers["X-API-Key"] == TK_A
    body = ingest_form(sent)
    assert CORPUS_A2 in body
    assert "# About us" in body
    # Nothing went to the scraped site.
    assert engine.other_requests == []


async def test_deliver_reports_per_page_failure(
    client: AsyncClient, db: Database, data_dir: Path
) -> None:
    job = await _make_job(db, CORPUS_A1, TENANT_A)
    await db.create_page(
        ScrapedPage(job_id=job.id, url="https://x/1", status=PageStatus.APPROVED, id="nofile")
    )
    resp = await client.post(f"/api/v1/jobs/{job.id}/deliver", headers=auth(CK_A1))
    assert resp.status_code == 200
    assert resp.json()["results"][0]["success"] is False
    assert (resp.json()["delivered"], resp.json()["failed"]) == (0, 1)
    page = await db.get_page("nofile")
    assert page is not None
    assert page.status == PageStatus.APPROVED
    assert page.delivery_error


async def test_approve_only_moves_verified_to_approved(
    client: AsyncClient, db: Database
) -> None:
    job = await _make_job(db, CORPUS_A1, TENANT_A)
    verified = ScrapedPage(job_id=job.id, url="https://x/v", status=PageStatus.VERIFIED)
    rejected = ScrapedPage(job_id=job.id, url="https://x/r", status=PageStatus.REJECTED)
    done = ScrapedPage(job_id=job.id, url="https://x/d", status=PageStatus.DELIVERED)
    for p in (verified, rejected, done):
        await db.create_page(p)
    resp = await client.post(
        f"/api/v1/jobs/{job.id}/approve",
        json={"page_ids": [verified.id, rejected.id, done.id]},
        headers=auth(TK_A),
    )
    assert resp.json()["approved"] == 1
    statuses = {p.id: p.status for p in await db.list_pages(job.id)}
    assert statuses == {
        verified.id: PageStatus.APPROVED,
        rejected.id: PageStatus.REJECTED,
        done.id: PageStatus.DELIVERED,
    }


async def test_retry_resends_only_failed_pages(
    client: AsyncClient, db: Database, data_dir: Path, engine: FakeEngine
) -> None:
    job = await _make_job(db, CORPUS_A1, TENANT_A)
    ok = ScrapedPage(job_id=job.id, url="https://x/ok", status=PageStatus.APPROVED, id="okpage")
    bad = ScrapedPage(job_id=job.id, url="https://x/bad", status=PageStatus.APPROVED, id="badpage")
    old = ScrapedPage(job_id=job.id, url="https://x/old", status=PageStatus.DELIVERED, id="oldpage")
    for p in (ok, bad, old):
        await db.create_page(p)
    _stage_page(data_dir, job.id, "okpage", "ok content")
    _stage_page(data_dir, job.id, "oldpage", "already delivered")

    first = await client.post(f"/api/v1/jobs/{job.id}/deliver", headers=auth(CK_A1))
    assert (first.json()["delivered"], first.json()["failed"]) == (1, 1)
    assert len(engine.ingests) == 1

    _stage_page(data_dir, job.id, "badpage", "fixed content")
    second = await client.post(f"/api/v1/jobs/{job.id}/deliver", headers=auth(CK_A1))
    assert second.json()["attempted"] == 1
    assert (second.json()["delivered"], second.json()["failed"]) == (1, 0)
    assert len(engine.ingests) == 2
    assert "fixed content" in ingest_form(engine.ingests[1])
    assert all("already delivered" not in ingest_form(r) for r in engine.ingests)
    fixed = await db.get_page("badpage")
    assert fixed is not None
    assert (fixed.status, fixed.delivery_error) == (PageStatus.DELIVERED, None)


async def test_deliver_legacy_job_without_corpus_is_409(
    client: AsyncClient, db: Database
) -> None:
    legacy = ScrapeJob(url="https://old.example.com")
    await db.create_job(legacy)
    resp = await client.post(f"/api/v1/jobs/{legacy.id}/deliver", headers=auth(ADMIN_KEY))
    assert resp.status_code == 409


# ── review_mode=false ────────────────────────────────────────────────


async def test_review_mode_off_auto_delivers_after_crawl(
    client: AsyncClient, db: Database, data_dir: Path, engine: FakeEngine
) -> None:
    async def fake_crawl(self: CrawlOrchestrator, job: ScrapeJob) -> None:
        assert self.allow_private is False
        good = ScrapedPage(job_id=job.id, url=f"{job.url}/a", status=PageStatus.VERIFIED)
        bad = ScrapedPage(job_id=job.id, url=f"{job.url}/b", status=PageStatus.REJECTED)
        await db.create_page(good)
        await db.create_page(bad)
        _stage_page(data_dir, job.id, good.id, "verified content")
        job.status = JobStatus.VERIFYING
        await db.update_job(job)

    with patch.object(CrawlOrchestrator, "crawl", fake_crawl):
        resp = await _submit(client, CK_A1, review_mode=False)
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]
        for _ in range(100):
            job = await db.get_job(job_id)
            if job is not None and job.status == JobStatus.COMPLETED:
                break
            await asyncio.sleep(0.01)

    job = await db.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.COMPLETED
    assert job.error is None
    assert len(engine.ingests) == 1
    sent = engine.ingests[0]
    assert str(sent.url) == f"{RAG_URL}/api/v1/ingest"
    assert sent.headers["X-API-Key"] == CK_A1
    assert CORPUS_A1 in ingest_form(sent)
    assert "verified content" in ingest_form(sent)
    delivered = await db.list_pages(job_id, status=PageStatus.DELIVERED)
    assert len(delivered) == 1


async def test_review_mode_on_waits_for_approval(
    client: AsyncClient, db: Database, engine: FakeEngine
) -> None:
    async def fake_crawl(self: CrawlOrchestrator, job: ScrapeJob) -> None:
        await db.create_page(
            ScrapedPage(job_id=job.id, url=f"{job.url}/a", status=PageStatus.VERIFIED)
        )
        job.status = JobStatus.VERIFYING
        await db.update_job(job)

    with patch.object(CrawlOrchestrator, "crawl", fake_crawl):
        job_id = (await _submit(client, CK_A1)).json()["job_id"]
        for _ in range(100):
            job = await db.get_job(job_id)
            if job is not None and job.status == JobStatus.VERIFYING:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)

    assert engine.ingests == []
    assert len(await db.list_pages(job_id, status=PageStatus.VERIFIED)) == 1
