"""tests/test_dashboard.py — server-rendered pages behind the sign-in cookie."""

import uuid

from httpx import AsyncClient

from scraper.dashboard.views import KEY_COOKIE
from scraper.storage.database import Database
from scraper.storage.models import PageStatus, ScrapedPage, ScrapeJob
from tests.conftest import CK_A1, CORPUS_A1, CORPUS_B1, TENANT_A, TENANT_B, TK_A, TK_B


async def _sign_in(client: AsyncClient, key: str) -> None:
    resp = await client.post(
        "/login",
        content=f"api_key={key}",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    client.cookies.set(KEY_COOKIE, key)


class TestDashboard:
    async def test_pages_redirect_to_login_without_key(self, client: AsyncClient) -> None:
        for path in ("/", f"/jobs/{uuid.uuid4().hex[:12]}", "/jobs/x/pages/y"):
            resp = await client.get(path)
            assert resp.status_code == 303
            assert resp.headers["location"] == "/login"

    async def test_login_page_renders(self, client: AsyncClient) -> None:
        resp = await client.get("/login")
        assert resp.status_code == 200
        assert 'name="api_key"' in resp.text

    async def test_bad_key_is_refused(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/login",
            content="api_key=nope",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 401
        assert KEY_COOKIE not in resp.headers.get("set-cookie", "")

    async def test_jobs_list_page(self, client: AsyncClient, db: Database) -> None:
        mine = ScrapeJob(url="https://mine.example.com", target_corpus_id=CORPUS_A1,
                         owner_tenant_id=TENANT_A)
        theirs = ScrapeJob(url="https://theirs.example.com", target_corpus_id=CORPUS_B1,
                           owner_tenant_id=TENANT_B)
        await db.create_job(mine)
        await db.create_job(theirs)
        await _sign_in(client, TK_A)
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Callobuzz Scraper" in resp.text
        assert mine.id in resp.text
        assert theirs.id not in resp.text

    async def test_job_detail_page(self, client: AsyncClient, db: Database) -> None:
        job = ScrapeJob(url="https://example.com", target_corpus_id=CORPUS_A1,
                        owner_tenant_id=TENANT_A)
        await db.create_job(job)
        await db.create_page(ScrapedPage(job_id=job.id, url="https://example.com/p", id="pg1"))
        await _sign_in(client, CK_A1)
        resp = await client.get(f"/jobs/{job.id}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "X-API-Key" in resp.text
        assert (await client.get(f"/jobs/{job.id}?status=rejected")).status_code == 200
        assert (await client.get(f"/jobs/{job.id}/pages/pg1")).status_code == 200

    async def test_approved_filter_and_page_detail(
        self, client: AsyncClient, db: Database
    ) -> None:
        job = ScrapeJob(url="https://example.com", target_corpus_id=CORPUS_A1,
                        owner_tenant_id=TENANT_A)
        await db.create_job(job)
        await db.create_page(ScrapedPage(
            job_id=job.id, url="javascript:alert(1)", id="pgx",
            status=PageStatus.APPROVED, delivery_error="RAG engine returned HTTP 500",
        ))
        await _sign_in(client, TK_A)
        listing = await client.get(f"/jobs/{job.id}?status=approved")
        assert listing.status_code == 200
        assert "RAG engine returned HTTP 500" in listing.text
        detail = await client.get(f"/jobs/{job.id}/pages/pgx")
        assert detail.status_code == 200
        assert "RAG engine returned HTTP 500" in detail.text
        assert 'href="javascript:' not in detail.text

    async def test_other_tenants_job_is_404(self, client: AsyncClient, db: Database) -> None:
        job = ScrapeJob(url="https://example.com", target_corpus_id=CORPUS_A1,
                        owner_tenant_id=TENANT_A)
        await db.create_job(job)
        await db.create_page(ScrapedPage(job_id=job.id, url="https://example.com/p", id="pg1"))
        await _sign_in(client, TK_B)
        assert (await client.get(f"/jobs/{job.id}")).status_code == 404
        assert (await client.get(f"/jobs/{job.id}/pages/pg1")).status_code == 404

    async def test_job_detail_not_found(self, client: AsyncClient) -> None:
        await _sign_in(client, TK_A)
        resp = await client.get(f"/jobs/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_logout_clears_cookie(self, client: AsyncClient) -> None:
        resp = await client.get("/logout")
        assert resp.status_code == 303
        assert KEY_COOKIE in resp.headers["set-cookie"]
