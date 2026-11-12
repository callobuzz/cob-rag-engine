"""Unit tests for FastAPI REST API endpoints."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.api.app import create_app

pytestmark = pytest.mark.usefixtures("unlimited_plan")


@pytest.fixture(autouse=True)
def _no_corpus_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests mock the session per route; the stats query is tested on its own."""
    from unittest.mock import AsyncMock as _AsyncMock

    monkeypatch.setattr("callobuzz_rag.api.routes.corpus.corpus_stats", _AsyncMock(return_value={}))


@pytest.fixture
def mock_session():
    """Mock AsyncSession for DB operations."""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.delete = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def mock_settings():
    """Mock Settings for app state."""
    settings = MagicMock()
    settings.cors_origins = ["*"]
    settings.s3_bucket = "test-bucket"
    settings.s3_endpoint = "http://localhost:9002"
    settings.s3_access_key = MagicMock()
    settings.s3_access_key.get_secret_value.return_value = "test-key"
    settings.s3_secret_key = MagicMock()
    settings.s3_secret_key.get_secret_value.return_value = "test-key"
    settings.s3_region = "us-east-1"
    settings.review_expiry_days = 7
    settings.max_upload_bytes = 50 * 1024 * 1024
    return settings


@pytest.fixture
def app(mock_session, mock_settings):
    """Create test app with mocked dependencies."""
    application = create_app()

    # Set mock settings on app state for routes that access request.app.state.settings
    application.state.settings = mock_settings

    # Mock session factory for health check (accesses app.state directly, not via DI)
    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    application.state.session_factory = MagicMock(return_value=mock_session_ctx)

    # Override the session dependency
    async def override_get_session():
        yield mock_session

    from callobuzz_rag.api.deps import get_session, require_admin

    application.dependency_overrides[get_session] = override_get_session
    application.dependency_overrides[require_admin] = lambda: None

    # Bypass APIKeyAuthMiddleware by patching its dispatch to set admin state
    from callobuzz_rag.api.auth import APIKeyAuthMiddleware

    original_dispatch = APIKeyAuthMiddleware.dispatch

    async def passthrough_dispatch(self, request, call_next):
        request.state.is_admin = True
        request.state.corpus = None
        return await call_next(request)

    APIKeyAuthMiddleware.dispatch = passthrough_dispatch
    yield application
    APIKeyAuthMiddleware.dispatch = original_dispatch


@pytest.fixture
async def client(app):
    """Async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --- Health check ---


async def test_health_check(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["services"]["api"] is True
    assert data["services"]["postgres"] is True  # Mock session works
    # Redis, Qdrant and file storage are not available in unit tests
    assert "redis" in data["services"]
    assert "qdrant" in data["services"]
    assert "storage" in data["services"]


async def test_health_reports_a_missing_bucket(client):
    """Listing buckets used to pass while the configured bucket was missing."""
    from unittest.mock import AsyncMock, patch

    with (
        patch(
            "callobuzz_rag.storage.factory.storage_reachable",
            new=AsyncMock(return_value=False),
        ),
        patch("callobuzz_rag.storage.factory.object_storage", return_value=object()),
    ):
        data = (await client.get("/api/v1/health")).json()
    assert data["services"]["storage"] is False
    assert data["status"] == "degraded"


# --- Corpus CRUD ---


async def test_create_corpus(client, mock_session):
    """POST /api/v1/corpora creates a corpus."""
    corpus_id = uuid.uuid4()

    async def mock_refresh(obj):
        obj.id = corpus_id
        obj.created_at = "2026-02-28T00:00:00+00:00"

    mock_session.refresh = AsyncMock(side_effect=mock_refresh)
    tenant = MagicMock(id=uuid.uuid4(), max_corpora=10)
    mock_session.get = AsyncMock(return_value=tenant)
    count = MagicMock()
    count.scalar.return_value = 0
    mock_session.execute = AsyncMock(return_value=count)

    # The assistant a new knowledge base starts with is covered by test_assistant_new_kb.py;
    # this stays a pure create test (exact add and commit counts below).
    with patch("callobuzz_rag.api.routes.corpus.publish_initial_assistant", new=AsyncMock()):
        resp = await client.post(
            "/api/v1/corpora",
            json={
                "name": "Test Corpus",
                "description": "A test corpus",
                "tenant_id": str(tenant.id),
            },
        )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Corpus"
    assert data["description"] == "A test corpus"
    assert mock_session.add.call_count == 2  # the knowledge base, then its default key
    mock_session.commit.assert_awaited_once()


async def test_create_corpus_validation(client):
    """POST /api/v1/corpora rejects empty name."""
    resp = await client.post("/api/v1/corpora", json={"name": ""})
    assert resp.status_code == 422


async def test_list_corpora(client, mock_session):
    """GET /api/v1/corpora returns list."""
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute = AsyncMock(return_value=mock_result)

    resp = await client.get("/api/v1/corpora")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["count"] == 0


async def test_get_corpus(client, mock_session):
    """GET /api/v1/corpora/{id} returns corpus."""
    corpus_id = uuid.uuid4()
    mock_corpus = MagicMock()
    mock_corpus.id = corpus_id
    mock_corpus.name = "Test"
    mock_corpus.description = None
    mock_corpus.qdrant_collection = f"corpus_{str(corpus_id)[:8]}"
    mock_corpus.tenant_id = uuid.uuid4()
    mock_corpus.llm_config = None
    mock_corpus.prompt_config = None
    mock_corpus.retrieval_config = None
    mock_corpus.embedding_config = None
    mock_corpus.applied_embedding = None
    mock_corpus.created_at = "2026-02-28T00:00:00+00:00"
    mock_session.get = AsyncMock(return_value=mock_corpus)

    resp = await client.get(f"/api/v1/corpora/{corpus_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Test"


async def test_get_corpus_not_found(client, mock_session):
    """GET /api/v1/corpora/{id} returns 404."""
    mock_session.get = AsyncMock(return_value=None)
    resp = await client.get(f"/api/v1/corpora/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_delete_corpus(client, mock_session):
    """DELETE /api/v1/corpora/{id}: one SQL delete, committed BEFORE the vectors go.

    The other order let a timeout leave a corpus with rows but no collection.
    """
    mock_corpus = MagicMock(qdrant_collection="corpus_abc", tenant_id=uuid.uuid4())
    mock_session.get = AsyncMock(return_value=mock_corpus)
    order: list[str] = []
    deleted = MagicMock()
    deleted.all.return_value = [(uuid.uuid4(), "corpus_abc")]  # DELETE ... RETURNING

    def execute(stmt):
        order.append(str(stmt).split()[0])
        return deleted

    mock_session.execute = AsyncMock(side_effect=execute)
    mock_session.commit = AsyncMock(side_effect=lambda: order.append("COMMIT"))
    vector_store = AsyncMock()
    vector_store.delete_collection = AsyncMock(
        side_effect=lambda name: order.append(f"drop {name}")
    )
    client._transport.app.state.vector_store = vector_store

    resp = await client.delete(f"/api/v1/corpora/{uuid.uuid4()}")

    assert resp.status_code == 204
    assert order == ["DELETE", "COMMIT", "drop corpus_abc"]
    mock_session.delete.assert_not_called()  # no ORM cascade loading every chunk


async def test_delete_corpus_not_found(client, mock_session):
    """DELETE /api/v1/corpora/{id} returns 404."""
    mock_session.get = AsyncMock(return_value=None)
    resp = await client.delete(f"/api/v1/corpora/{uuid.uuid4()}")
    assert resp.status_code == 404


# --- Ingest endpoints ---


@patch("callobuzz_rag.worker.tasks.ingest_document_task")
@patch("callobuzz_rag.api.routes.ingest.object_storage")
async def test_upload_document(mock_s3_cls, mock_task, client, mock_session):
    """POST /api/v1/ingest accepts file upload and dispatches Celery task."""
    corpus_id = uuid.uuid4()
    job_id = uuid.uuid4()

    # Mock S3 storage
    mock_storage = AsyncMock()
    mock_s3_cls.return_value = mock_storage

    # Corpus lookup
    mock_corpus = MagicMock()
    mock_session.get = AsyncMock(return_value=mock_corpus)

    async def mock_refresh(obj):
        obj.id = job_id

    mock_session.refresh = AsyncMock(side_effect=mock_refresh)

    resp = await client.post(
        "/api/v1/ingest",
        data={"corpus_id": str(corpus_id)},
        files={"file": ("test.pdf", b"%PDF-1.4 fake pdf content", "application/pdf")},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "queued"
    assert data["job_id"] == str(job_id)
    mock_storage.upload.assert_awaited_once()
    mock_task.delay.assert_called_once()


async def test_upload_unsupported_type(client, mock_session):
    """POST /api/v1/ingest rejects unsupported file type."""
    corpus_id = uuid.uuid4()
    mock_corpus = MagicMock()
    mock_session.get = AsyncMock(return_value=mock_corpus)

    resp = await client.post(
        "/api/v1/ingest",
        data={"corpus_id": str(corpus_id)},
        files={"file": ("test.exe", b"binary", "application/x-msdownload")},
    )
    assert resp.status_code == 400
    assert "Unsupported file type" in resp.json()["detail"]


async def test_upload_file_too_large(client, mock_session):
    """POST /api/v1/ingest rejects files over 50MB."""
    corpus_id = uuid.uuid4()
    mock_corpus = MagicMock()
    mock_session.get = AsyncMock(return_value=mock_corpus)

    large_content = b"x" * (50 * 1024 * 1024 + 1)
    resp = await client.post(
        "/api/v1/ingest",
        data={"corpus_id": str(corpus_id)},
        files={"file": ("big.pdf", large_content, "application/pdf")},
    )
    assert resp.status_code == 413
    assert resp.json()["code"] == "upload_too_large"


async def test_upload_corpus_not_found(client, mock_session):
    """POST /api/v1/ingest returns 404 for missing corpus."""
    mock_session.get = AsyncMock(return_value=None)

    resp = await client.post(
        "/api/v1/ingest",
        data={"corpus_id": str(uuid.uuid4())},
        files={"file": ("test.pdf", b"content", "application/pdf")},
    )
    assert resp.status_code == 404


async def test_get_job_status(client, mock_session):
    """GET /api/v1/ingest/jobs/{id} returns job."""
    job_id = uuid.uuid4()
    mock_job = MagicMock()
    mock_job.id = job_id
    mock_job.status = "completed"
    mock_job.total_documents = 1
    mock_job.processed_documents = 1
    mock_job.errors = None
    mock_job.created_at = "2026-02-28T00:00:00+00:00"
    mock_job.finished_at = None
    mock_job.records = None
    mock_job.corpus_id = None
    mock_job.file_name = "a.pdf"
    mock_job.mode = "auto"
    mock_job.target_document_id = None
    mock_job.expires_at = None
    mock_job.similar_documents = None
    mock_session.get = AsyncMock(return_value=mock_job)

    resp = await client.get(f"/api/v1/ingest/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


async def test_get_job_not_found(client, mock_session):
    """GET /api/v1/ingest/jobs/{id} returns 404."""
    mock_session.get = AsyncMock(return_value=None)
    resp = await client.get(f"/api/v1/ingest/jobs/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_list_jobs(client, mock_session):
    """GET /api/v1/ingest/jobs returns list."""
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute = AsyncMock(return_value=mock_result)

    resp = await client.get("/api/v1/ingest/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["count"] == 0


async def test_upload_content_type_mismatch(client, mock_session):
    """POST /api/v1/ingest rejects file whose content doesn't match declared type."""
    corpus_id = uuid.uuid4()
    mock_corpus = MagicMock()
    mock_session.get = AsyncMock(return_value=mock_corpus)

    # Send plain text content declared as PDF
    resp = await client.post(
        "/api/v1/ingest",
        data={"corpus_id": str(corpus_id)},
        files={"file": ("fake.pdf", b"This is just plain text", "application/pdf")},
    )
    assert resp.status_code == 400
    assert "does not match" in resp.json()["detail"]


# --- Schema tests ---


def test_ingest_response_defaults():
    from callobuzz_rag.api.schemas.ingest import IngestResponse

    resp = IngestResponse(job_id=uuid.uuid4(), status="queued")
    assert resp.message == "Document accepted for processing"


def test_health_response_model():
    from callobuzz_rag.api.schemas.health import HealthResponse

    resp = HealthResponse(status="ok", services={"api": True, "db": False})
    assert resp.services["api"] is True
    assert resp.services["db"] is False


# --- Audio uploads ---

_WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32


async def test_upload_audio_refused_without_transcription(client, mock_session, mock_settings):
    """Audio is refused with a clear message when no transcription provider is set."""
    mock_settings.transcription_provider = "off"
    mock_session.get = AsyncMock(return_value=MagicMock())

    resp = await client.post(
        "/api/v1/ingest",
        data={"corpus_id": str(uuid.uuid4())},
        files={"file": ("call.wav", _WAV, "audio/wav")},
    )
    assert resp.status_code == 400
    assert "RAG_TRANSCRIPTION_PROVIDER" in resp.json()["detail"]


@patch("callobuzz_rag.worker.tasks.ingest_document_task")
@patch("callobuzz_rag.api.routes.ingest.object_storage")
async def test_upload_audio_accepted_with_transcription(
    mock_s3_cls, mock_task, client, mock_session, mock_settings
):
    """Audio is queued when transcription is configured, even from a generic client type."""
    mock_settings.transcription_provider = "whisper_api"
    mock_s3_cls.return_value = AsyncMock()
    mock_session.get = AsyncMock(return_value=MagicMock())

    async def mock_refresh(obj):
        obj.id = uuid.uuid4()

    mock_session.refresh = AsyncMock(side_effect=mock_refresh)

    resp = await client.post(
        "/api/v1/ingest",
        data={"corpus_id": str(uuid.uuid4())},
        files={"file": ("call.wav", _WAV, "application/octet-stream")},
    )
    assert resp.status_code == 202
    mock_task.delay.assert_called_once()


async def test_upload_audio_content_mismatch(client, mock_session, mock_settings):
    """A text file renamed to .mp3 is rejected by the signature check."""
    mock_settings.transcription_provider = "whisper_api"
    mock_session.get = AsyncMock(return_value=MagicMock())

    resp = await client.post(
        "/api/v1/ingest",
        data={"corpus_id": str(uuid.uuid4())},
        files={"file": ("song.mp3", b"just some text", "audio/mpeg")},
    )
    assert resp.status_code == 400
    assert "does not match" in resp.json()["detail"]


@pytest.mark.parametrize(
    "head",
    [
        b"ID3\x04\x00" + b"\x00" * 11,
        b"\xff\xfb\x90\x00" + b"\x00" * 12,
        b"OggS" + b"\x00" * 12,
        b"fLaC" + b"\x00" * 12,
        b"\x1aE\xdf\xa3" + b"\x00" * 12,
        b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 4,
        _WAV,
    ],
)
def test_audio_signatures(head):
    from callobuzz_rag.api.routes.ingest import _looks_like_audio

    assert _looks_like_audio(head)
