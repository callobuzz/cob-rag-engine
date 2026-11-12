"""Dashboard sign-ins: the scraper accepts ``Authorization: Bearer`` (dashboard D8 part A)."""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pytest_httpx import HTTPXMock

from scraper.api import auth as auth_module
from scraper.api.app import create_app
from scraper.api.auth import BEARER_CACHE_MAX_SECONDS, Credential, Identity, IdentityCache
from scraper.api.routes import REVIEW_MODE_REQUIRED
from scraper.config import ScraperSettings
from scraper.storage.database import Database
from scraper.storage.models import PageStatus, ScrapedPage, ScrapeJob
from tests.conftest import (
    ADMIN_KEY,
    BEARER_A,
    BEARER_ADMIN,
    BEARER_EXPIRED,
    BEARER_REVOKED,
    BEARER_SUSPENDED,
    CORPUS_A1,
    CORPUS_A2,
    CORPUS_B1,
    TENANT_A,
    TENANT_B,
    TK_B,
    FakeEngine,
    auth,
    bearer,
)


@pytest.fixture
def no_crawl() -> Iterator[AsyncMock]:
    with patch("scraper.api.routes.start_crawl_background", new_callable=AsyncMock) as m:
        yield m


async def _make_job(db: Database, corpus_id: str, tenant_id: str | None) -> ScrapeJob:
    job = ScrapeJob(
        url="https://vidya.example.in", target_corpus_id=corpus_id, owner_tenant_id=tenant_id
    )
    await db.create_job(job)
    return job


async def _approved_page(db: Database, data_dir: Path, job: ScrapeJob, text: str) -> ScrapedPage:
    page = ScrapedPage(job_id=job.id, url=f"{job.url}/fees", status=PageStatus.APPROVED)
    await db.create_page(page)
    job_dir = data_dir / "jobs" / job.id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / f"{page.id}.md").write_text(text, encoding="utf-8")
    return page


def _sent(httpx_mock: HTTPXMock, path_part: str) -> list[httpx.Request]:
    return [r for r in httpx_mock.get_requests() if path_part in r.url.path]


# ── Identity ─────────────────────────────────────────────────────────


async def test_bearer_lists_its_tenants_jobs(client: AsyncClient, db: Database) -> None:
    job_a = await _make_job(db, CORPUS_A1, TENANT_A)
    await _make_job(db, CORPUS_B1, TENANT_B)
    resp = await client.get("/api/v1/jobs", headers=bearer(BEARER_A))
    assert resp.status_code == 200
    assert [j["id"] for j in resp.json()] == [job_a.id]


async def test_super_admin_session_sees_every_job(client: AsyncClient, db: Database) -> None:
    await _make_job(db, CORPUS_A1, TENANT_A)
    await _make_job(db, CORPUS_B1, TENANT_B)
    resp = await client.get("/api/v1/jobs", headers=bearer(BEARER_ADMIN))
    assert len(resp.json()) == 2


async def test_whoami_is_asked_with_the_bearer(client: AsyncClient, httpx_mock: HTTPXMock) -> None:
    assert (await client.get("/api/v1/jobs", headers=bearer(BEARER_A))).status_code == 200
    whoami = _sent(httpx_mock, "/whoami")
    assert len(whoami) == 1
    assert whoami[0].headers["Authorization"] == f"Bearer {BEARER_A}"
    assert "X-API-Key" not in whoami[0].headers


async def test_scheme_is_case_insensitive(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/jobs", headers={"Authorization": f"bearer {BEARER_A}"})
    assert resp.status_code == 200


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Bearer", "Bearer   ", "Token abc"])
async def test_other_authorization_values_are_401(
    client: AsyncClient, engine: FakeEngine, header: str
) -> None:
    resp = await client.get("/api/v1/jobs", headers={"Authorization": header})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Credential required"
    assert engine.whoami_calls == 0


async def test_api_key_wins_when_both_are_sent(
    client: AsyncClient, db: Database, httpx_mock: HTTPXMock
) -> None:
    """Like the engine: an Authorization header added by a proxy changes nothing for a key."""
    job_b = await _make_job(db, CORPUS_B1, TENANT_B)
    resp = await client.get(
        "/api/v1/jobs", headers={"X-API-Key": TK_B, "Authorization": f"Bearer {BEARER_A}"}
    )
    assert [j["id"] for j in resp.json()] == [job_b.id]
    whoami = _sent(httpx_mock, "/whoami")
    assert whoami[0].headers["X-API-Key"] == TK_B
    assert "Authorization" not in whoami[0].headers


# ── Engine codes passed through ──────────────────────────────────────


async def test_expired_token_is_passed_through_with_its_code(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/jobs", headers=bearer(BEARER_EXPIRED))
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Access token expired", "code": "token_expired"}


async def test_revoked_session_is_passed_through(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/jobs", headers=bearer(BEARER_REVOKED))
    assert resp.status_code == 401
    assert resp.json()["code"] == "session_revoked"


async def test_workspace_not_active_is_403_with_its_status(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/jobs", headers=bearer(BEARER_SUSPENDED))
    assert resp.status_code == 403
    assert resp.json() == {
        "detail": "This workspace is not active",
        "code": "workspace_not_active",
        "status": "suspended",
    }


async def test_bad_key_keeps_the_engines_code(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/jobs", headers=auth("ck_wrong"))
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Invalid API key", "code": "invalid_api_key"}


# ── Cache ────────────────────────────────────────────────────────────


async def test_bearer_identity_is_cached(client: AsyncClient, engine: FakeEngine) -> None:
    for _ in range(3):
        assert (await client.get("/api/v1/jobs", headers=bearer(BEARER_A))).status_code == 200
    assert engine.whoami_calls == 1


def test_bearer_cache_ttl_is_at_most_30_seconds() -> None:
    assert BEARER_CACHE_MAX_SECONDS == 30
    cache = IdentityCache(ttl_seconds=600)
    assert cache.ttl_for(Credential("bearer", "t")) == 30
    assert cache.ttl_for(Credential("api_key", "k")) == 600
    assert IdentityCache(ttl_seconds=10).ttl_for(Credential("bearer", "t")) == 10


def test_bearer_cache_entry_expires_after_30_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(auth_module.time, "monotonic", lambda: now[0])
    cache = IdentityCache(ttl_seconds=600)
    token, key = Credential("bearer", "same"), Credential("api_key", "same")
    cache.put(token, Identity(kind="tenant", tenant_id=TENANT_A))
    cache.put(key, Identity(kind="admin"))
    now[0] += 29
    assert cache.get(token) == Identity(kind="tenant", tenant_id=TENANT_A)
    now[0] += 2
    assert cache.get(token) is None
    # The same secret presented as a key is a separate entry with the key TTL.
    assert cache.get(key) == Identity(kind="admin")


def test_credential_value_is_not_in_its_repr() -> None:
    assert BEARER_A not in repr(Credential("bearer", BEARER_A))
    assert Credential("bearer", "t").headers() == {"Authorization": "Bearer t"}
    assert Credential("api_key", "k").headers() == {"X-API-Key": "k"}


# ── Create ───────────────────────────────────────────────────────────


async def test_bearer_creates_a_review_mode_job(
    client: AsyncClient, db: Database, no_crawl: AsyncMock, httpx_mock: HTTPXMock
) -> None:
    resp = await client.post(
        "/api/v1/scrape",
        json={"urls": ["https://vidya.example.in/fees"], "target_corpus_id": CORPUS_A2},
        headers=bearer(BEARER_A),
    )
    assert resp.status_code == 202
    job = await db.get_job(resp.json()["job_id"])
    assert job is not None
    assert (job.target_corpus_id, job.owner_tenant_id, job.review_mode) == (
        CORPUS_A2,
        TENANT_A,
        True,
    )
    corpus_check = _sent(httpx_mock, "/corpora/")
    assert corpus_check[0].headers["Authorization"] == f"Bearer {BEARER_A}"
    no_crawl.assert_awaited_once()
    caller = no_crawl.await_args.args[2] if no_crawl.await_args else None
    assert caller is not None
    assert caller.credential == Credential("bearer", BEARER_A)


async def test_bearer_cannot_use_another_tenants_corpus(
    client: AsyncClient, db: Database, no_crawl: AsyncMock
) -> None:
    resp = await client.post(
        "/api/v1/scrape",
        json={"urls": ["https://example.com"], "target_corpus_id": CORPUS_B1},
        headers=bearer(BEARER_A),
    )
    assert resp.status_code == 403
    assert await db.list_jobs() == []


async def test_bearer_with_review_mode_off_is_422(
    client: AsyncClient, db: Database, no_crawl: AsyncMock
) -> None:
    for token in (BEARER_A, BEARER_ADMIN):
        resp = await client.post(
            "/api/v1/scrape",
            json={
                "urls": ["https://example.com"],
                "target_corpus_id": CORPUS_A1,
                "review_mode": False,
            },
            headers=bearer(token),
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == REVIEW_MODE_REQUIRED
    assert await db.list_jobs() == []
    no_crawl.assert_not_awaited()


async def test_a_key_may_still_turn_review_mode_off(
    client: AsyncClient, no_crawl: AsyncMock
) -> None:
    resp = await client.post(
        "/api/v1/scrape",
        json={"urls": ["https://example.com"], "target_corpus_id": CORPUS_A1, "review_mode": False},
        headers=auth(ADMIN_KEY),
    )
    assert resp.status_code == 202


async def test_token_expired_at_the_corpus_check_is_passed_through(
    client: AsyncClient, engine: FakeEngine, no_crawl: AsyncMock
) -> None:
    """The identity is cached; the token expired since. The dashboard must refresh."""
    assert (await client.get("/api/v1/jobs", headers=bearer(BEARER_A))).status_code == 200
    engine.expired_after_whoami.add(BEARER_A)
    resp = await client.post(
        "/api/v1/scrape",
        json={"urls": ["https://example.com"], "target_corpus_id": CORPUS_A1},
        headers=bearer(BEARER_A),
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "token_expired"
    no_crawl.assert_not_awaited()


# ── Approve and deliver ──────────────────────────────────────────────


async def test_bearer_approves_and_delivers_with_its_own_authorization(
    client: AsyncClient, db: Database, data_dir: Path, engine: FakeEngine
) -> None:
    job = await _make_job(db, CORPUS_A1, TENANT_A)
    page = ScrapedPage(job_id=job.id, url=f"{job.url}/batches", status=PageStatus.VERIFIED)
    await db.create_page(page)
    job_dir = data_dir / "jobs" / job.id
    job_dir.mkdir(parents=True)
    (job_dir / f"{page.id}.md").write_text("# Batches for 2026-27", encoding="utf-8")

    approve = await client.post(
        f"/api/v1/jobs/{job.id}/approve",
        json={"page_ids": [page.id]},
        headers=bearer(BEARER_A),
    )
    assert approve.json() == {"approved": 1}
    resp = await client.post(f"/api/v1/jobs/{job.id}/deliver", headers=bearer(BEARER_A))
    assert resp.status_code == 200
    assert (resp.json()["delivered"], resp.json()["failed"]) == (1, 0)
    sent = engine.ingests[0]
    assert sent.headers["Authorization"] == f"Bearer {BEARER_A}"
    assert "X-API-Key" not in sent.headers


async def test_token_expired_mid_delivery_is_a_401_not_a_page_error(
    client: AsyncClient, db: Database, data_dir: Path, engine: FakeEngine
) -> None:
    job = await _make_job(db, CORPUS_A1, TENANT_A)
    page = await _approved_page(db, data_dir, job, "# Fees")
    assert (await client.get("/api/v1/jobs", headers=bearer(BEARER_A))).status_code == 200
    engine.expired_after_whoami.add(BEARER_A)

    resp = await client.post(f"/api/v1/jobs/{job.id}/deliver", headers=bearer(BEARER_A))
    assert resp.status_code == 401
    assert resp.json()["code"] == "token_expired"
    untouched = await db.get_page(page.id)
    assert untouched is not None
    assert (untouched.status, untouched.delivery_error) == (PageStatus.APPROVED, None)


async def test_page_error_carries_the_engines_reason(
    db: Database, data_dir: Path, settings: ScraperSettings, httpx_mock: HTTPXMock
) -> None:
    def engine(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/whoami":
            return httpx.Response(200, json={"kind": "tenant", "tenant_id": TENANT_A})
        return httpx.Response(
            409,
            json={
                "detail": "Your plan allows 5 documents (5 used; this adds 1).",
                "code": "document_limit_reached",
                "limit": 5,
            },
        )

    httpx_mock.add_callback(engine, is_reusable=True)
    job = await _make_job(db, CORPUS_A1, TENANT_A)
    page = await _approved_page(db, data_dir, job, "# Fees")
    app = create_app(db=db, data_dir=str(data_dir), settings=settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(f"/api/v1/jobs/{job.id}/deliver", headers=bearer(BEARER_A))

    assert resp.status_code == 200
    error = "RAG engine returned HTTP 409: Your plan allows 5 documents (5 used; this adds 1)."
    assert resp.json()["results"] == [{"page_id": page.id, "success": False, "error": error}]
    failed = await db.get_page(page.id)
    assert failed is not None
    assert (failed.status, failed.delivery_error) == (PageStatus.APPROVED, error)


# ── CORS ─────────────────────────────────────────────────────────────


async def test_cors_preflight_allows_authorization(client: AsyncClient) -> None:
    resp = await client.options(
        "/api/v1/jobs",
        headers={
            "Origin": "http://127.0.0.1:8090",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert resp.status_code == 200
    assert "authorization" in resp.headers["access-control-allow-headers"].lower()
