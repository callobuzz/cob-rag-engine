"""Tests for multi-corpus ingest pipeline integration (Epic 4-08)."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from callobuzz_rag.api.routes.ingest import _resolve_corpus_for_ingest

pytestmark = pytest.mark.usefixtures("unlimited_plan")


def _make_corpus(
    corpus_id: uuid.UUID | None = None,
    qdrant_collection: str = "corpus_chunks_abc",
) -> MagicMock:
    """Create a mock Corpus object."""
    c = MagicMock()
    c.id = corpus_id or uuid.uuid4()
    c.qdrant_collection = qdrant_collection
    return c


def _make_request(
    corpus: MagicMock | None = None,
    is_admin: bool = False,
) -> MagicMock:
    """Create a mock Request with state attributes.

    `tenant` must be pinned to None. Left unset on a MagicMock it auto-creates a
    truthy child mock, so the routes' `getattr(request.state, "tenant", None)` took
    the tenant branch and raised `403: Corpus does not belong to this tenant` in tests
    that have nothing to do with tenants.
    """
    request = MagicMock()
    request.state.corpus = corpus
    request.state.is_admin = is_admin
    request.state.tenant = None
    return request


class TestResolveCorpusForIngest:
    """Tests for _resolve_corpus_for_ingest."""

    def test_auth_corpus_derives_id_and_collection(self) -> None:
        corpus = _make_corpus()
        request = _make_request(corpus=corpus)

        cid, collection = _resolve_corpus_for_ingest(request, None)

        assert cid == corpus.id
        assert collection == corpus.qdrant_collection

    def test_mismatch_corpus_id_returns_403(self) -> None:
        corpus = _make_corpus()
        request = _make_request(corpus=corpus)
        other_id = uuid.uuid4()

        with pytest.raises(HTTPException) as exc_info:
            _resolve_corpus_for_ingest(request, other_id)
        assert exc_info.value.status_code == 403
        assert "does not match" in str(exc_info.value.detail)

    def test_admin_requires_corpus_id(self) -> None:
        request = _make_request(is_admin=True)

        with pytest.raises(HTTPException) as exc_info:
            _resolve_corpus_for_ingest(request, None)
        assert exc_info.value.status_code == 422
        assert "required" in str(exc_info.value.detail)

    def test_admin_with_corpus_id_resolves(self) -> None:
        request = _make_request(is_admin=True)
        cid = uuid.uuid4()

        resolved_id, collection = _resolve_corpus_for_ingest(request, cid)

        assert resolved_id == cid
        assert collection == ""  # admin must look up from DB


class TestIngestPassesCollection:
    """Tests that upload_document passes qdrant_collection to the Celery task."""

    @pytest.mark.asyncio
    async def test_qdrant_collection_passed_to_task(self) -> None:
        """The Celery task should receive the corpus's qdrant_collection."""
        from callobuzz_rag.api.routes.ingest import upload_document

        corpus = _make_corpus(qdrant_collection="corpus_chunks_xyz")
        request = _make_request(corpus=corpus)
        request.app.state.settings = MagicMock()
        request.app.state.settings.s3_bucket = "test-bucket"
        request.app.state.settings.review_expiry_days = 7
        request.app.state.settings.max_upload_bytes = 50 * 1024 * 1024

        mock_file = MagicMock()
        mock_file.content_type = "application/pdf"
        mock_file.filename = "test.pdf"
        mock_file.read = AsyncMock(return_value=b"%PDF-1.4 small content")

        mock_session = AsyncMock()
        mock_corpus_obj = _make_corpus(corpus_id=corpus.id, qdrant_collection="corpus_chunks_xyz")
        mock_session.get = AsyncMock(return_value=mock_corpus_obj)

        mock_job = MagicMock()
        mock_job.id = uuid.uuid4()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock(side_effect=lambda j: setattr(j, "id", mock_job.id))

        with (
            patch("callobuzz_rag.api.routes.ingest.object_storage") as mock_storage_cls,
            patch("callobuzz_rag.worker.tasks.ingest_document_task") as mock_task,
        ):
            mock_storage_cls.return_value.upload = AsyncMock()
            mock_task.delay = MagicMock()

            await upload_document(
                request=request,
                file=mock_file,
                corpus_id=None,
                idempotency_key=None,
                mode="auto",
                target_document_id=None,
                auto_confirm=False,
                # Calling the handler directly means FastAPI never resolves defaults,
                # so an omitted `metadata` arrives as the truthy `Form(...)` object
                # and `json.loads` chokes on it. Production passes None here.
                metadata=None,
                session=mock_session,
            )

            mock_task.delay.assert_called_once()
            call_kwargs = mock_task.delay.call_args.kwargs
            assert call_kwargs["qdrant_collection"] == "corpus_chunks_xyz"
            assert call_kwargs["corpus_id"] == str(corpus.id)

    @pytest.mark.asyncio
    async def test_storage_key_includes_corpus_id(self) -> None:
        """The S3 storage key should include the corpus ID."""
        from callobuzz_rag.api.routes.ingest import upload_document

        corpus = _make_corpus()
        request = _make_request(corpus=corpus)
        request.app.state.settings = MagicMock()
        request.app.state.settings.s3_bucket = "test-bucket"
        request.app.state.settings.review_expiry_days = 7
        request.app.state.settings.max_upload_bytes = 50 * 1024 * 1024

        mock_file = MagicMock()
        mock_file.content_type = "application/pdf"
        mock_file.filename = "test.pdf"
        mock_file.read = AsyncMock(return_value=b"%PDF-1.4 small content")

        mock_session = AsyncMock()
        mock_corpus_obj = _make_corpus(corpus_id=corpus.id)
        mock_session.get = AsyncMock(return_value=mock_corpus_obj)

        mock_job = MagicMock()
        mock_job.id = uuid.uuid4()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock(side_effect=lambda j: setattr(j, "id", mock_job.id))

        with (
            patch("callobuzz_rag.api.routes.ingest.object_storage") as mock_storage_cls,
            patch("callobuzz_rag.worker.tasks.ingest_document_task") as mock_task,
        ):
            mock_storage_cls.return_value.upload = AsyncMock()
            mock_task.delay = MagicMock()

            await upload_document(
                request=request,
                file=mock_file,
                corpus_id=None,
                idempotency_key=None,
                mode="auto",
                target_document_id=None,
                auto_confirm=False,
                # Calling the handler directly means FastAPI never resolves defaults,
                # so an omitted `metadata` arrives as the truthy `Form(...)` object
                # and `json.loads` chokes on it. Production passes None here.
                metadata=None,
                session=mock_session,
            )

            call_kwargs = mock_task.delay.call_args.kwargs
            assert str(corpus.id) in call_kwargs["storage_key"]


class TestWorkerUsesCorpusCollection:
    """Tests that the worker pipeline uses corpus-specific Qdrant collection."""

    @pytest.mark.asyncio
    async def test_worker_uses_corpus_collection(self) -> None:
        """_run_pipeline should use qdrant_collection when provided."""
        from callobuzz_rag.worker.tasks import _run_pipeline

        corpus_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        with (
            patch("callobuzz_rag.worker.tasks.get_settings") as mock_settings_fn,
            patch("callobuzz_rag.db.engine.create_engine") as mock_engine_fn,
            patch("callobuzz_rag.db.engine.create_session_factory") as mock_sf_fn,
            patch("callobuzz_rag.worker.tasks._create_providers") as mock_providers,
            patch("callobuzz_rag.pipeline.orchestrator.IngestionOrchestrator") as mock_orch_cls,
            patch("callobuzz_rag.worker.tasks._use_corpus_embedder", AsyncMock()),
        ):
            settings = MagicMock()
            settings.qdrant_collection = "default_collection"
            settings.s3_bucket = "test-bucket"
            mock_settings_fn.return_value = settings

            mock_engine = AsyncMock()
            mock_engine.dispose = AsyncMock()
            mock_engine_fn.return_value = mock_engine

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_sf_fn.return_value = MagicMock(return_value=mock_session)

            providers = {
                "storage": AsyncMock(),
                "parser": MagicMock(),
                "chunker": MagicMock(),
                "enricher": MagicMock(),
                "embedder": AsyncMock(),
                "vector_store": AsyncMock(),
            }
            providers["storage"].download = AsyncMock(return_value=b"file data")
            mock_providers.return_value = providers

            mock_result = MagicMock()
            mock_result.status = "completed"
            mock_result.chunks_created = 5
            mock_result.vectors_stored = 5
            mock_result.error = None
            mock_orch_cls.return_value.ingest = AsyncMock(return_value=mock_result)

            await _run_pipeline(
                storage_key="corpus/abc/raw/doc1",
                file_name="test.pdf",
                mime_type="application/pdf",
                corpus_id=corpus_id,
                job_id=job_id,
                qdrant_collection="corpus_chunks_custom",
            )

            call_kwargs = mock_orch_cls.call_args.kwargs
            assert call_kwargs["collection"] == "corpus_chunks_custom"

    @pytest.mark.asyncio
    async def test_worker_falls_back_to_default(self) -> None:
        """_run_pipeline should fall back to settings.qdrant_collection."""
        from callobuzz_rag.worker.tasks import _run_pipeline

        corpus_id = str(uuid.uuid4())
        job_id = str(uuid.uuid4())

        with (
            patch("callobuzz_rag.worker.tasks.get_settings") as mock_settings_fn,
            patch("callobuzz_rag.db.engine.create_engine") as mock_engine_fn,
            patch("callobuzz_rag.db.engine.create_session_factory") as mock_sf_fn,
            patch("callobuzz_rag.worker.tasks._create_providers") as mock_providers,
            patch("callobuzz_rag.pipeline.orchestrator.IngestionOrchestrator") as mock_orch_cls,
            patch("callobuzz_rag.worker.tasks._use_corpus_embedder", AsyncMock()),
        ):
            settings = MagicMock()
            settings.qdrant_collection = "default_collection"
            settings.s3_bucket = "test-bucket"
            mock_settings_fn.return_value = settings

            mock_engine = AsyncMock()
            mock_engine.dispose = AsyncMock()
            mock_engine_fn.return_value = mock_engine

            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_sf_fn.return_value = MagicMock(return_value=mock_session)

            providers = {
                "storage": AsyncMock(),
                "parser": MagicMock(),
                "chunker": MagicMock(),
                "enricher": MagicMock(),
                "embedder": AsyncMock(),
                "vector_store": AsyncMock(),
            }
            providers["storage"].download = AsyncMock(return_value=b"file data")
            mock_providers.return_value = providers

            mock_result = MagicMock()
            mock_result.status = "completed"
            mock_result.chunks_created = 5
            mock_result.vectors_stored = 5
            mock_result.error = None
            mock_orch_cls.return_value.ingest = AsyncMock(return_value=mock_result)

            await _run_pipeline(
                storage_key="corpus/abc/raw/doc1",
                file_name="test.pdf",
                mime_type="application/pdf",
                corpus_id=corpus_id,
                job_id=job_id,
                qdrant_collection=None,
            )

            call_kwargs = mock_orch_cls.call_args.kwargs
            assert call_kwargs["collection"] == "default_collection"


class TestIngestJobScoping:
    """Tests for job listing scoping and cross-corpus access control."""

    @pytest.mark.asyncio
    async def test_list_jobs_scoped_to_corpus(self) -> None:
        """Non-admin users see only jobs for their corpus."""
        from callobuzz_rag.api.routes.ingest import list_jobs

        corpus = _make_corpus()
        request = _make_request(corpus=corpus)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_result.scalar.return_value = 0

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await list_jobs(
            request, status=None, corpus_id=None, offset=0, limit=20, session=mock_session
        )
        assert result.count == 0

        # Both the count and the page are scoped to the corpus
        assert mock_session.execute.await_count == 2
        executed_query = mock_session.execute.call_args[0][0]
        compiled = str(executed_query.compile(compile_kwargs={"literal_binds": True}))
        assert "corpus_id" in compiled

    @pytest.mark.asyncio
    async def test_admin_sees_all_jobs(self) -> None:
        """Admin users see all jobs except the system corpus's (no corpus scoping)."""
        from callobuzz_rag.api.routes.ingest import list_jobs

        request = _make_request(is_admin=True)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_result.scalar.return_value = 0

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await list_jobs(
            request, status=None, corpus_id=None, offset=0, limit=20, session=mock_session
        )
        assert result.count == 0

        # No caller scoping: admin sees every job except the built-in COB corpus (is_system)
        executed_query = mock_session.execute.call_args[0][0]
        compiled = str(executed_query.compile(compile_kwargs={"literal_binds": True}))
        assert "is_system" in compiled
        assert "corpus_id IN" not in compiled

    @pytest.mark.asyncio
    async def test_get_job_status_rejects_cross_corpus_access(self) -> None:
        from callobuzz_rag.api.routes.ingest import get_job_status

        corpus_a = _make_corpus(corpus_id=uuid.uuid4())
        corpus_b_id = uuid.uuid4()
        request = _make_request(corpus=corpus_a)

        mock_job = MagicMock()
        mock_job.corpus_id = corpus_b_id

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_job)

        with pytest.raises(HTTPException) as exc_info:
            await get_job_status(request, uuid.uuid4(), mock_session)
        assert exc_info.value.status_code == 403
        assert "outside this key's scope" in str(exc_info.value.detail)
