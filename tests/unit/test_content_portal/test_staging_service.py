"""Unit tests for content_portal/services/staging_service.py."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.content_portal.services.staging_service import (
    MAX_STAGED_FILE_SIZE,
    StagingError,
    StagingService,
    build_portal_storage_key,
)


class TestBuildPortalStorageKey:
    def test_standard_key_format(self) -> None:
        """Key follows portal/{corpus}/{workspace}/{content}/{filename} pattern."""
        corpus_id = uuid.uuid4()
        workspace_id = uuid.uuid4()
        content_id = uuid.uuid4()
        key = build_portal_storage_key(corpus_id, workspace_id, content_id, "report.pdf")
        assert key == f"portal/{corpus_id}/{workspace_id}/{content_id}/report.pdf"

    def test_slashes_in_filename_sanitized(self) -> None:
        """Forward and back slashes in filename are replaced with underscores."""
        corpus_id = uuid.uuid4()
        workspace_id = uuid.uuid4()
        content_id = uuid.uuid4()
        key = build_portal_storage_key(corpus_id, workspace_id, content_id, "path/to\\file.txt")
        assert "path_to_file.txt" in key
        assert "/" not in key.split("/")[-1].replace("_", "")

    def test_empty_filename(self) -> None:
        """Empty filename produces valid key."""
        corpus_id = uuid.uuid4()
        workspace_id = uuid.uuid4()
        content_id = uuid.uuid4()
        key = build_portal_storage_key(corpus_id, workspace_id, content_id, "")
        assert key.endswith("/untitled")

    @pytest.mark.parametrize("name", [".", "..", "...", "\x00"])
    def test_dot_names_become_untitled(self, name: str) -> None:
        key = build_portal_storage_key(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), name)
        assert key.endswith("/untitled")

    async def test_long_hindi_name_is_accepted_by_local_storage(self, tmp_path) -> None:
        import io

        from callobuzz_rag.storage.local import LocalFileStorageProvider

        name = "\u092c\u094d\u0930\u094b\u0936\u0930" * 24 + ".pdf"
        key = build_portal_storage_key(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), name)
        assert key.endswith(".pdf")
        assert len(key.rsplit("/", 1)[-1].encode()) <= 200
        store = LocalFileStorageProvider(tmp_path / "files")
        await store.ensure_bucket("b")
        await store.upload("b", key, io.BytesIO(b"x"))
        assert await store.exists("b", key)
        dots = build_portal_storage_key(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "..")
        await store.upload("b", dots, io.BytesIO(b"y"))
        assert await store.exists("b", dots)


def _make_settings() -> MagicMock:
    """Create a mock Settings object with S3 config."""
    settings = MagicMock()
    settings.s3_bucket = "test-bucket"
    settings.s3_endpoint = "http://localhost:9002"
    settings.s3_access_key = MagicMock()
    settings.s3_access_key.get_secret_value.return_value = "test-key"
    settings.s3_secret_key = MagicMock()
    settings.s3_secret_key.get_secret_value.return_value = "test-key"
    settings.s3_region = "us-east-1"
    return settings


class TestStagingServiceUploadFile:
    @pytest.mark.asyncio
    async def test_upload_file_creates_record(self) -> None:
        """upload_file stores file in file storage and creates StagedContent record."""
        session = AsyncMock()
        settings = _make_settings()

        mock_file = MagicMock()
        mock_file.filename = "report.pdf"
        mock_file.content_type = "application/pdf"
        mock_file.read = AsyncMock(return_value=b"fake pdf content")

        workspace_id = uuid.uuid4()
        corpus_id = uuid.uuid4()

        with patch(
            "callobuzz_rag.content_portal.services.staging_service.object_storage"
        ) as mock_storage_cls:
            mock_storage = AsyncMock()
            mock_storage_cls.return_value = mock_storage

            with patch(
                "callobuzz_rag.content_portal.models.staged_content.StagedContent",
                create=True,
            ) as mock_model_cls:
                mock_staged = MagicMock()
                mock_staged.id = uuid.uuid4()
                mock_staged.workspace_id = workspace_id
                mock_staged.source_type = "file"
                mock_staged.original_filename = "report.pdf"
                mock_staged.ai_status = "pending"
                mock_model_cls.return_value = mock_staged

                svc = StagingService(session, settings)
                result = await svc.upload_file(workspace_id, corpus_id, mock_file)

                mock_storage.upload.assert_called_once()
                session.add.assert_called_once()
                session.flush.assert_called_once()
                assert result.source_type == "file"

    @pytest.mark.asyncio
    async def test_upload_file_rejects_oversized(self) -> None:
        """upload_file raises StagingError for files over 50MB."""
        session = AsyncMock()
        settings = _make_settings()

        oversized_content = b"x" * (MAX_STAGED_FILE_SIZE + 1)
        mock_file = MagicMock()
        mock_file.filename = "huge.pdf"
        mock_file.content_type = "application/pdf"
        mock_file.read = AsyncMock(return_value=oversized_content)

        with patch("callobuzz_rag.content_portal.services.staging_service.object_storage"):
            svc = StagingService(session, settings)
            with pytest.raises(StagingError, match="File too large"):
                await svc.upload_file(uuid.uuid4(), uuid.uuid4(), mock_file)

    @pytest.mark.asyncio
    async def test_upload_file_handles_storage_failure(self) -> None:
        """upload_file wraps storage exceptions in StagingError."""
        session = AsyncMock()
        settings = _make_settings()

        mock_file = MagicMock()
        mock_file.filename = "report.pdf"
        mock_file.content_type = "application/pdf"
        mock_file.read = AsyncMock(return_value=b"content")

        with patch(
            "callobuzz_rag.content_portal.services.staging_service.object_storage"
        ) as mock_storage_cls:
            mock_storage = AsyncMock()
            mock_storage.upload.side_effect = ConnectionError("storage down")
            mock_storage_cls.return_value = mock_storage

            svc = StagingService(session, settings)
            with pytest.raises(StagingError, match="Failed to upload to object storage"):
                await svc.upload_file(uuid.uuid4(), uuid.uuid4(), mock_file)


class TestStagingServiceUploadText:
    @pytest.mark.asyncio
    async def test_upload_text_stores_as_txt(self) -> None:
        """upload_text encodes text as UTF-8, stores in file storage, creates record."""
        session = AsyncMock()
        settings = _make_settings()

        workspace_id = uuid.uuid4()
        corpus_id = uuid.uuid4()

        with patch(
            "callobuzz_rag.content_portal.services.staging_service.object_storage"
        ) as mock_storage_cls:
            mock_storage = AsyncMock()
            mock_storage_cls.return_value = mock_storage

            with patch(
                "callobuzz_rag.content_portal.models.staged_content.StagedContent",
                create=True,
            ) as mock_model_cls:
                mock_staged = MagicMock()
                mock_staged.id = uuid.uuid4()
                mock_staged.source_type = "text_dump"
                mock_staged.ai_status = "pending"
                mock_staged.mime_type = "text/plain"
                mock_model_cls.return_value = mock_staged

                svc = StagingService(session, settings)
                result = await svc.upload_text(
                    workspace_id, corpus_id, "Some messy text", title="My Notes"
                )

                mock_storage.upload.assert_called_once()
                call_args = mock_storage.upload.call_args
                assert call_args[0][2].read() == b"Some messy text"
                assert result.source_type == "text_dump"


class TestStagingServiceUploadUrl:
    @pytest.mark.asyncio
    async def test_upload_url_stores_url_string(self) -> None:
        """upload_url stores URL as text file in file storage."""
        session = AsyncMock()
        settings = _make_settings()

        workspace_id = uuid.uuid4()
        corpus_id = uuid.uuid4()

        with patch(
            "callobuzz_rag.content_portal.services.staging_service.object_storage"
        ) as mock_storage_cls:
            mock_storage = AsyncMock()
            mock_storage_cls.return_value = mock_storage

            with patch(
                "callobuzz_rag.content_portal.models.staged_content.StagedContent",
                create=True,
            ) as mock_model_cls:
                mock_staged = MagicMock()
                mock_staged.id = uuid.uuid4()
                mock_staged.source_type = "url"
                mock_staged.ai_status = "pending"
                mock_model_cls.return_value = mock_staged

                svc = StagingService(session, settings)
                result = await svc.upload_url(
                    workspace_id,
                    corpus_id,
                    "https://example.com/docs",
                    title="Example Docs",
                )

                mock_storage.upload.assert_called_once()
                assert result.source_type == "url"


class TestStagingServiceReprocess:
    @pytest.mark.asyncio
    async def test_reprocess_resets_status(self) -> None:
        """reprocess_content sets ai_status back to pending."""
        session = AsyncMock()
        settings = _make_settings()

        mock_staged = MagicMock()
        mock_staged.ai_status = "failed"
        mock_staged.ai_output_type = "chunks"
        mock_staged.ai_quality_report = {"some": "data"}
        mock_staged.token_usage = {"prompt_tokens": 100}
        mock_staged.id = uuid.uuid4()

        with patch("callobuzz_rag.content_portal.services.staging_service.object_storage"):
            svc = StagingService(session, settings)
            svc.get_content = AsyncMock(return_value=mock_staged)  # type: ignore[method-assign]

            result = await svc.reprocess_content(uuid.uuid4(), mock_staged.id, force=False)

            assert result.ai_status == "pending"
            assert result.ai_output_type is None
            assert result.ai_quality_report is None
            assert result.token_usage is None

    @pytest.mark.asyncio
    async def test_reprocess_rejects_when_processing(self) -> None:
        """reprocess_content raises when content is currently processing."""
        session = AsyncMock()
        settings = _make_settings()

        mock_staged = MagicMock()
        mock_staged.ai_status = "processing"
        mock_staged.id = uuid.uuid4()

        with patch("callobuzz_rag.content_portal.services.staging_service.object_storage"):
            svc = StagingService(session, settings)
            svc.get_content = AsyncMock(return_value=mock_staged)  # type: ignore[method-assign]

            with pytest.raises(StagingError, match="already being processed"):
                await svc.reprocess_content(uuid.uuid4(), mock_staged.id)

    @pytest.mark.asyncio
    async def test_reprocess_requires_force_when_completed(self) -> None:
        """reprocess_content raises unless force=True when already completed."""
        session = AsyncMock()
        settings = _make_settings()

        mock_staged = MagicMock()
        mock_staged.ai_status = "completed"
        mock_staged.id = uuid.uuid4()

        with patch("callobuzz_rag.content_portal.services.staging_service.object_storage"):
            svc = StagingService(session, settings)
            svc.get_content = AsyncMock(return_value=mock_staged)  # type: ignore[method-assign]

            with pytest.raises(StagingError, match="force=true"):
                await svc.reprocess_content(uuid.uuid4(), mock_staged.id, force=False)

    @pytest.mark.asyncio
    async def test_reprocess_not_found_raises(self) -> None:
        """reprocess_content raises StagingError when content not found."""
        session = AsyncMock()
        settings = _make_settings()

        with patch("callobuzz_rag.content_portal.services.staging_service.object_storage"):
            svc = StagingService(session, settings)
            svc.get_content = AsyncMock(return_value=None)  # type: ignore[method-assign]

            with pytest.raises(StagingError, match="not found"):
                await svc.reprocess_content(uuid.uuid4(), uuid.uuid4())


class TestStagingServiceDispatch:
    def test_dispatch_ai_transform_calls_celery(self) -> None:
        """dispatch_ai_transform sends a Celery task."""
        session = AsyncMock()
        settings = _make_settings()

        content_id = uuid.uuid4()
        corpus_id = uuid.uuid4()

        with patch("callobuzz_rag.content_portal.services.staging_service.object_storage"):
            svc = StagingService(session, settings)

            with patch(
                "callobuzz_rag.content_portal.tasks.transform_tasks.transform_content_task"
            ) as mock_task:
                mock_result = MagicMock()
                mock_result.id = "task-123"
                mock_task.delay.return_value = mock_result

                task_id = svc.dispatch_ai_transform(content_id, corpus_id)

                mock_task.delay.assert_called_once_with(
                    staged_content_id=str(content_id),
                    corpus_id=str(corpus_id),
                    model_override=None,
                )
                assert task_id == "task-123"
