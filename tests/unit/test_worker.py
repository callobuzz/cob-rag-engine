"""Unit tests for Celery worker and tasks."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.worker.celery_app import create_celery_app

# --- Celery app tests ---


def test_create_celery_app():
    """Celery app is created with correct config."""
    app = create_celery_app()
    assert app.main == "callobuzz_rag"
    assert app.conf.task_serializer == "json"
    assert app.conf.task_acks_late is True
    assert app.conf.task_track_started is True
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.task_default_queue == "ingestion"


def test_celery_app_module_level():
    """Module-level celery_app is available."""
    from callobuzz_rag.worker.celery_app import celery_app

    assert celery_app is not None
    assert celery_app.main == "callobuzz_rag"


# --- Task tests ---


def test_task_is_registered():
    """ingest_document_task is registered with correct name."""
    # Force-import tasks module so autodiscover takes effect
    import callobuzz_rag.worker.tasks  # noqa: F401
    from callobuzz_rag.worker.celery_app import celery_app

    assert "callobuzz_rag.ingest_document" in celery_app.tasks


@patch("callobuzz_rag.worker.tasks.asyncio")
def test_ingest_document_task_calls_pipeline(mock_asyncio):
    """Task calls asyncio.run with _run_pipeline."""
    from callobuzz_rag.worker.tasks import ingest_document_task

    mock_asyncio.run.return_value = {
        "status": "completed",
        "chunks_created": 5,
        "vectors_stored": 5,
        "error": None,
    }

    corpus_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    # Call the task's run() method directly (bypasses Celery worker dispatch)
    result = ingest_document_task.apply(
        args=["corpus/123/raw/456", "test.pdf", "application/pdf", corpus_id, job_id],
    ).result

    assert result["status"] == "completed"
    assert result["chunks_created"] == 5
    mock_asyncio.run.assert_called_once()


# --- _run_pipeline tests ---


@patch("callobuzz_rag.worker.tasks.get_settings")
@patch("callobuzz_rag.worker.tasks._create_providers")
@patch("callobuzz_rag.db.engine.create_engine")
@patch("callobuzz_rag.db.engine.create_session_factory")
async def test_run_pipeline(mock_sf, mock_engine, mock_providers, mock_settings):
    """_run_pipeline orchestrates download + pipeline execution."""
    from callobuzz_rag.worker.tasks import _run_pipeline

    # Setup settings
    settings = MagicMock()
    settings.s3_bucket = "test-bucket"
    settings.qdrant_collection = "chunks"
    mock_settings.return_value = settings

    # Setup providers
    mock_storage = AsyncMock()
    mock_storage.download = AsyncMock(return_value=b"file content")
    mock_embedder = AsyncMock()
    mock_vector_store = AsyncMock()
    mock_providers.return_value = {
        "storage": mock_storage,
        "parser": AsyncMock(),
        "chunker": MagicMock(),
        "enricher": AsyncMock(),
        "embedder": mock_embedder,
        "vector_store": mock_vector_store,
    }

    # Setup DB session
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.flush = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.get = AsyncMock(return_value=None)
    mock_session.add = MagicMock()
    mock_session.add_all = MagicMock()

    mock_session_ctx = AsyncMock()
    mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_sf.return_value = MagicMock(return_value=mock_session_ctx)

    mock_engine_inst = AsyncMock()
    mock_engine.return_value = mock_engine_inst

    corpus_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    result = await _run_pipeline(
        storage_key="corpus/123/raw/456",
        file_name="test.html",
        mime_type="text/html",
        corpus_id=corpus_id,
        job_id=job_id,
    )

    assert result["status"] in ("completed", "failed")
    mock_storage.download.assert_awaited_once_with("test-bucket", "corpus/123/raw/456")
    mock_embedder.close.assert_awaited_once()
    mock_vector_store.close.assert_awaited_once()
    mock_engine_inst.dispose.assert_awaited_once()


# --- Provider factory tests ---


def test_create_providers():
    """_create_providers returns dict with all required keys."""
    # A REAL Settings, not a MagicMock. The mock answered every attribute,
    # including `s3_addressing_style` — which botocore then rejected as an
    # invalid addressing style, because a MagicMock is not "auto"/"virtual"/
    # "path". A mock that invents attributes cannot tell you which settings a
    # factory actually reads, and it silently absorbs the ones you forget.
    from pydantic import SecretStr

    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.worker.tasks import _create_providers

    settings = Settings(
        s3_endpoint="http://127.0.0.1:9002",
        s3_access_key=SecretStr("test-key"),
        s3_secret_key=SecretStr("test-key"),
        s3_region="us-east-1",
        jina_api_key=SecretStr("test-key"),
        qdrant_collection="chunks",
        embedding_provider="jina",
        embedding_model="jina-embeddings-v3",
        embedding_dimensions=1024,
    )

    providers = _create_providers(settings)

    assert "storage" in providers
    assert "parser" in providers
    assert "chunker" in providers
    assert "enricher" in providers
    assert "embedder" in providers
    assert "vector_store" in providers


# --- transient embedding failures are retried -------------------------------


def test_transient_embedding_failure_is_detected():
    from callobuzz_rag.worker.tasks import _is_transient_embedding_failure

    base = {"status": "failed", "failed_at_step": "embedding"}
    assert _is_transient_embedding_failure(
        {**base, "error": "RetryError[<Future ... raised ConnectError>]"}
    )
    assert not _is_transient_embedding_failure({**base, "error": "401 Unauthorized"})
    # Only the embedding step: a later failure may already have written vectors.
    assert not _is_transient_embedding_failure(
        {**base, "failed_at_step": "storing", "error": "ConnectError"}
    )
    assert not _is_transient_embedding_failure(
        {"status": "completed", "failed_at_step": None, "error": None}
    )


@patch("callobuzz_rag.worker.tasks.asyncio")
def test_ingest_task_retries_when_embedding_service_is_down(mock_asyncio):
    from celery.exceptions import Retry

    from callobuzz_rag.worker.tasks import ingest_document_task

    mock_asyncio.run.return_value = {
        "status": "failed",
        "failed_at_step": "embedding",
        "error": "RetryError[<Future at 0x1 state=finished raised ConnectError>]",
        "chunks_created": 0,
        "vectors_stored": 0,
    }
    with (
        patch.object(ingest_document_task, "retry", side_effect=Retry()) as retry,
        patch.object(ingest_document_task, "update_state"),
        pytest.raises(Retry),
    ):
        ingest_document_task.run("k", "f.md", "text/markdown", str(uuid.uuid4()), str(uuid.uuid4()))
    assert retry.call_args.kwargs["countdown"] == 30
