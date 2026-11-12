"""Shared fixtures: an app wired to a fake RAG engine (pytest-httpx)."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pytest_httpx import HTTPXMock

from scraper.api.app import create_app
from scraper.config import ScraperSettings
from scraper.storage.database import Database

RAG_URL = "http://rag.test"

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
CORPUS_A1 = "aaaaaaaa-0000-0000-0000-000000000001"
CORPUS_A2 = "aaaaaaaa-0000-0000-0000-000000000002"
CORPUS_B1 = "bbbbbbbb-0000-0000-0000-000000000001"

ADMIN_KEY = "admin-secret"
TK_A = "tk_tenant_a"
TK_B = "tk_tenant_b"
CK_A1 = "ck_corpus_a1"
CK_B1 = "ck_corpus_b1"

IDENTITIES: dict[str, dict[str, str | None]] = {
    ADMIN_KEY: {"kind": "admin", "tenant_id": None, "corpus_id": None},
    TK_A: {"kind": "tenant", "tenant_id": TENANT_A, "corpus_id": None},
    TK_B: {"kind": "tenant", "tenant_id": TENANT_B, "corpus_id": None},
    CK_A1: {"kind": "corpus", "tenant_id": TENANT_A, "corpus_id": CORPUS_A1},
    CK_B1: {"kind": "corpus", "tenant_id": TENANT_B, "corpus_id": CORPUS_B1},
}
CORPUS_OWNER = {CORPUS_A1: TENANT_A, CORPUS_A2: TENANT_A, CORPUS_B1: TENANT_B}

# Dashboard sign-ins (Authorization: Bearer): an owner of tenant A, a super admin,
# an expired token, a revoked session and a suspended workspace.
BEARER_A = "eyJ.owner-a.sig"
BEARER_ADMIN = "eyJ.superadmin.sig"
BEARER_EXPIRED = "eyJ.expired.sig"
BEARER_REVOKED = "eyJ.revoked.sig"
BEARER_SUSPENDED = "eyJ.suspended.sig"
SESSIONS: dict[str, dict[str, str | None]] = {
    BEARER_A: {"kind": "tenant", "tenant_id": TENANT_A, "corpus_id": None, "auth": "session"},
    BEARER_ADMIN: {"kind": "admin", "tenant_id": None, "corpus_id": None, "auth": "session"},
}
BEARER_ERRORS: dict[str, tuple[int, dict[str, str]]] = {
    BEARER_EXPIRED: (401, {"detail": "Access token expired", "code": "token_expired"}),
    BEARER_REVOKED: (
        401,
        {"detail": "This session has ended. Sign in again.", "code": "session_revoked"},
    ),
    BEARER_SUSPENDED: (
        403,
        {
            "detail": "This workspace is not active",
            "code": "workspace_not_active",
            "status": "suspended",
        },
    ),
}


PUBLIC_IP = "93.184.215.14"


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """No test resolves real names: every host is PUBLIC_IP unless a test maps it."""
    table: dict[str, list[str]] = {}

    async def resolve(host: str, port: int) -> list[str]:
        if host in table:
            if not table[host]:
                raise OSError("no such host")
            return table[host]
        return [PUBLIC_IP]

    monkeypatch.setattr("scraper.crawl.netguard.resolve_host", resolve)
    return table


def auth(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class FakeEngine:
    """Records what the scraper sent to the engine."""

    whoami_calls: int = 0
    # Tokens the engine now answers 401 token_expired for on every route but whoami
    # (the scraper's cached identity outlived the token).
    expired_after_whoami: set[str] = field(default_factory=set)
    ingests: list[httpx.Request] = field(default_factory=list)
    other_requests: list[httpx.Request] = field(default_factory=list)


@pytest.fixture
def engine(httpx_mock: HTTPXMock) -> FakeEngine:
    """A fake engine: whoami, corpora/{id} ownership, and ingest."""
    fake = FakeEngine()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != "rag.test":
            fake.other_requests.append(request)
            return httpx.Response(599)
        path = request.url.path
        key = request.headers.get("X-API-Key")
        token = None
        authorization = request.headers.get("Authorization", "")
        if not key and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        if token is not None:
            if token in BEARER_ERRORS:
                status, body = BEARER_ERRORS[token]
                if path == "/api/v1/whoami":
                    fake.whoami_calls += 1
                return httpx.Response(status, json=body)
            if token in fake.expired_after_whoami and path != "/api/v1/whoami":
                return httpx.Response(401, json=BEARER_ERRORS[BEARER_EXPIRED][1])
            ident = SESSIONS.get(token)
        else:
            ident = IDENTITIES.get(key or "")
        if path == "/api/v1/whoami":
            fake.whoami_calls += 1
            if ident is None:
                return httpx.Response(
                    401, json={"detail": "Invalid API key", "code": "invalid_api_key"}
                )
            return httpx.Response(200, json=ident)
        if ident is None:
            return httpx.Response(401)
        if path.startswith("/api/v1/corpora/"):
            corpus_id = path.rsplit("/", 1)[-1]
            if ident["kind"] == "corpus":
                return httpx.Response(403)
            if corpus_id not in CORPUS_OWNER:
                return httpx.Response(404)
            if ident["kind"] == "tenant" and CORPUS_OWNER[corpus_id] != ident["tenant_id"]:
                return httpx.Response(403)
            return httpx.Response(200, json={"id": corpus_id})
        if path == "/api/v1/ingest" and request.method == "POST":
            request.read()
            fake.ingests.append(request)
            return httpx.Response(202, json={"job_id": "ingest-1"})
        return httpx.Response(404)

    httpx_mock.add_callback(handler, is_reusable=True, is_optional=True)
    return fake


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database
    await database.close()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def settings(tmp_path: Path, data_dir: Path) -> ScraperSettings:
    return ScraperSettings(
        rag_url=RAG_URL,
        data_dir=str(data_dir),
        db_path=str(tmp_path / "test.db"),
    )


@pytest.fixture
async def client(
    db: Database, data_dir: Path, settings: ScraperSettings, engine: FakeEngine
) -> AsyncIterator[AsyncClient]:
    app = create_app(db=db, data_dir=str(data_dir), settings=settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def ingest_form(request: httpx.Request) -> str:
    """The raw multipart body of an ingest request, decoded for assertions."""
    return request.content.decode("utf-8", errors="replace")

