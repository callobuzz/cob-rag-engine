"""Staging service — upload raw content, create records, dispatch AI tasks."""

import io
import logging
import uuid as uuid_mod
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import ContentPortalError
from callobuzz_rag.storage.factory import object_storage

logger = logging.getLogger(__name__)

MAX_STAGED_FILE_SIZE = 50 * 1024 * 1024  # 50MB


class StagingError(ContentPortalError):
    """Content staging operation failed."""


class WorkspaceGoneError(StagingError):
    """The workspace was deleted while content was being staged into it."""


class StagedContentNotFoundError(StagingError):
    """No staged content with that id in this workspace (another workspace's, or none)."""


class StagingStorageError(StagingError):
    """Object storage refused or could not be reached; nothing was stored."""


def build_portal_storage_key(
    corpus_id: UUID,
    workspace_id: UUID,
    content_id: UUID,
    filename: str,
) -> str:
    """Build the storage key for portal staged content.

    Pattern: portal/{corpus_id}/{workspace_id}/{content_id}/{filename}
    """
    safe_filename = filename.replace("/", "_").replace("\\", "_").replace("\x00", "")
    if safe_filename.strip(".") == "":
        safe_filename = "untitled"
    if len(safe_filename.encode()) > 200:  # a file name is at most 255 bytes on most systems
        stem, dot, ext = safe_filename.rpartition(".")
        if not dot:
            stem, ext = safe_filename, ""
        room = 200 - (len(ext.encode()) + 1 if ext else 0)
        safe_filename = stem.encode()[:room].decode("utf-8", "ignore") + (f".{ext}" if ext else "")
    return f"portal/{corpus_id}/{workspace_id}/{content_id}/{safe_filename}"


class StagingService:
    """Manages content staging: upload, record creation, AI dispatch."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._storage = object_storage(settings)

    async def _store(self, staged: "StagedContent", data: bytes, content_type: str) -> None:  # type: ignore[name-defined]
        """Write the row (flushed), THEN the file, so the file never outlives its row.

        The row's foreign key share-locks the workspace row: a concurrent workspace
        delete waits for this transaction and then removes the file with the rest; a
        delete that already committed fails the flush before anything is stored. If the
        caller's commit fails afterwards it calls `discard` for the file.

        Raises:
            WorkspaceGoneError: The workspace no longer exists.
            StagingStorageError: The upload failed (the row is rolled back by the caller).
        """
        from sqlalchemy.exc import IntegrityError

        self._session.add(staged)
        try:
            await self._session.flush()
        except IntegrityError as e:
            await self._session.rollback()
            raise WorkspaceGoneError("Workspace not found") from e
        try:
            await self._storage.upload(
                self._settings.s3_bucket,
                staged.raw_storage_key,
                io.BytesIO(data),
                content_type=content_type,
            )
        except Exception as e:
            raise StagingStorageError(f"Failed to upload to object storage: {e}") from e
        await self._session.refresh(staged)

    async def discard(self, storage_key: str) -> None:
        """Delete a staged file whose row was not committed. Never raises."""
        from callobuzz_rag.storage.cleanup import discard_object

        await discard_object(
            self._storage, self._settings.s3_bucket, storage_key, reason="staged row not saved"
        )

    async def upload_file(
        self,
        workspace_id: UUID,
        corpus_id: UUID,
        file: UploadFile,
    ) -> "StagedContent":  # type: ignore[name-defined]
        """Upload a file to file storage and create a StagedContent record.

        Args:
            workspace_id: The workspace to stage content into.
            corpus_id: The corpus this workspace belongs to.
            file: The uploaded file.

        Returns:
            The created StagedContent record.

        Raises:
            StagingError: If upload fails or file is too large.
        """
        from callobuzz_rag.content_portal.models.staged_content import StagedContent

        contents = await file.read()
        if len(contents) > MAX_STAGED_FILE_SIZE:
            raise StagingError(
                f"File too large ({len(contents)} bytes). "
                f"Maximum: {MAX_STAGED_FILE_SIZE} bytes (50MB)"
            )

        content_id = uuid_mod.uuid4()
        filename = file.filename or "untitled"
        mime_type = file.content_type or "application/octet-stream"

        storage_key = build_portal_storage_key(corpus_id, workspace_id, content_id, filename)
        staged = StagedContent(
            id=content_id,
            workspace_id=workspace_id,
            source_type="file",
            original_filename=filename,
            mime_type=mime_type,
            raw_storage_key=storage_key,
            ai_status="pending",
        )
        await self._store(staged, contents, mime_type)

        logger.info(
            "Staged file content %s in workspace %s (key=%s)",
            content_id,
            workspace_id,
            storage_key,
        )
        return staged

    async def upload_text(
        self,
        workspace_id: UUID,
        corpus_id: UUID,
        content: str,
        title: str | None = None,
    ) -> "StagedContent":  # type: ignore[name-defined]
        """Store a text dump in file storage and create a StagedContent record.

        Args:
            workspace_id: The workspace to stage content into.
            corpus_id: The corpus this workspace belongs to.
            content: The raw text content.
            title: Optional title for the content.

        Returns:
            The created StagedContent record.
        """
        from callobuzz_rag.content_portal.models.staged_content import StagedContent

        content_id = uuid_mod.uuid4()
        filename = f"{title or 'text_dump'}.txt"

        storage_key = build_portal_storage_key(corpus_id, workspace_id, content_id, filename)
        staged = StagedContent(
            id=content_id,
            workspace_id=workspace_id,
            source_type="text_dump",
            original_filename=filename,
            mime_type="text/plain",
            raw_storage_key=storage_key,
            ai_status="pending",
        )
        await self._store(staged, content.encode("utf-8"), "text/plain")

        logger.info("Staged text content %s in workspace %s", content_id, workspace_id)
        return staged

    async def upload_url(
        self,
        workspace_id: UUID,
        corpus_id: UUID,
        url: str,
        title: str | None = None,
    ) -> "StagedContent":  # type: ignore[name-defined]
        """Create a StagedContent record for a URL (AI transformer fetches later).

        The URL string is stored in file storage as a small text file so that all staged
        content has a consistent storage_key. The AI transformer reads this key
        to retrieve the URL and fetch its content.

        Args:
            workspace_id: The workspace to stage content into.
            corpus_id: The corpus this workspace belongs to.
            url: The URL to stage.
            title: Optional title for the content.

        Returns:
            The created StagedContent record.
        """
        from callobuzz_rag.content_portal.models.staged_content import StagedContent

        content_id = uuid_mod.uuid4()
        filename = f"{title or 'url_source'}.url"

        storage_key = build_portal_storage_key(corpus_id, workspace_id, content_id, filename)
        staged = StagedContent(
            id=content_id,
            workspace_id=workspace_id,
            source_type="url",
            original_filename=filename,
            mime_type="text/uri-list",
            raw_storage_key=storage_key,
            ai_status="pending",
        )
        await self._store(staged, url.encode("utf-8"), "text/uri-list")

        logger.info(
            "Staged URL content %s in workspace %s (url=%s)",
            content_id,
            workspace_id,
            url,
        )
        return staged

    async def get_content(
        self,
        workspace_id: UUID,
        content_id: UUID,
    ) -> "StagedContent | None":  # type: ignore[name-defined]
        """Get a single staged content item by ID within a workspace.

        Returns:
            The StagedContent record, or None if not found.
        """
        from callobuzz_rag.content_portal.models.staged_content import StagedContent

        stmt = select(StagedContent).where(
            StagedContent.id == content_id,
            StagedContent.workspace_id == workspace_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_content(
        self,
        workspace_id: UUID,
    ) -> "list[StagedContent]":  # type: ignore[name-defined]
        """List all staged content in a workspace, ordered by creation time.

        Returns:
            List of StagedContent records.
        """
        from callobuzz_rag.content_portal.models.staged_content import StagedContent

        stmt = (
            select(StagedContent)
            .where(StagedContent.workspace_id == workspace_id)
            .order_by(StagedContent.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def reprocess_content(
        self,
        workspace_id: UUID,
        content_id: UUID,
        force: bool = False,
        model_override: str | None = None,
    ) -> "StagedContent":  # type: ignore[name-defined]
        """Re-run AI transformation on staged content.

        Args:
            workspace_id: The workspace containing the content.
            content_id: The content to reprocess.
            force: If True, reprocess even if already completed.
            model_override: Override the LLM model for this run.

        Returns:
            The updated StagedContent record.

        Raises:
            StagingError: If content not found or already processing.
        """
        staged = await self.get_content(workspace_id, content_id)
        if staged is None:
            raise StagedContentNotFoundError(
                f"Staged content {content_id} not found in workspace {workspace_id}"
            )

        if staged.ai_status == "processing":
            raise StagingError(f"Content {content_id} is already being processed")

        if staged.ai_status == "completed" and not force:
            raise StagingError(
                f"Content {content_id} already processed. Use force=true to reprocess."
            )

        staged.ai_status = "pending"
        staged.ai_output_type = None
        staged.ai_quality_report = None
        staged.token_usage = None
        await self._session.flush()
        await self._session.refresh(staged)

        logger.info(
            "Marked content %s for reprocessing (force=%s, model=%s)",
            content_id,
            force,
            model_override,
        )
        return staged

    def dispatch_ai_transform(
        self,
        content_id: UUID,
        corpus_id: UUID,
        model_override: str | None = None,
    ) -> str:
        """Dispatch a Celery task to run AI transformation on staged content.

        Args:
            content_id: The staged content ID to process.
            corpus_id: The corpus (for BYOK key resolution).
            model_override: Override the LLM model for this run.

        Returns:
            The Celery task ID.
        """
        from callobuzz_rag.content_portal.tasks.transform_tasks import (
            transform_content_task,
        )

        result = transform_content_task.delay(
            staged_content_id=str(content_id),
            corpus_id=str(corpus_id),
            model_override=model_override,
        )
        logger.info(
            "Dispatched AI transform task %s for content %s",
            result.id,
            content_id,
        )
        return str(result.id)
