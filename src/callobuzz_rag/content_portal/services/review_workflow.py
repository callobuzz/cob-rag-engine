"""Review workflow service — submit, approve, reject, ingest, audit trail."""

import io
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.core.exceptions import ContentPortalError

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ReviewWorkflowError(ContentPortalError):
    """Base exception for review workflow errors."""


class InvalidTransitionError(ReviewWorkflowError):
    """Workspace status transition is not allowed."""


class MakerCheckerViolationError(ReviewWorkflowError):
    """The same user cannot both create and approve in maker-checker mode."""


class WrongWorkspaceKindError(ReviewWorkflowError):
    """Approve / ingest on a post-ingest review workspace, which is published with
    finalize-review instead (ingesting it would duplicate the reviewed document)."""


def _refuse_review_workspace(workspace: object, action: str) -> None:
    from callobuzz_rag.content_portal.models.workspace import WorkspaceKind

    if getattr(workspace, "kind", None) == WorkspaceKind.POST_INGEST_REVIEW:
        raise WrongWorkspaceKindError(
            f"Cannot {action} a post-ingest review workspace: publish it with "
            "POST /api/v1/portal/workspaces/{id}/finalize-review, which makes the review a "
            "new version of the reviewed document"
        )


class IngestStorageError(ReviewWorkflowError):
    """Object storage could not take the packaged workspace; nothing was stored."""


class ChunkValidationError(ReviewWorkflowError):
    """Chunk-level validation failed (e.g., rejected chunks in submission)."""


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[str, list[str]] = {
    "draft": ["in_review"],
    "in_review": ["approved", "draft"],
    "approved": ["ingested"],
    "ingested": [],
    "archived": [],
}


def _validate_transition(current: str, target: str) -> None:
    """Validate that a workspace status transition is allowed.

    Args:
        current: Current workspace status.
        target: Desired target status.

    Raises:
        InvalidTransitionError: If the transition is not allowed.
    """
    allowed = VALID_TRANSITIONS.get(current, [])
    if target not in allowed:
        raise InvalidTransitionError(
            f"Cannot transition from '{current}' to '{target}'. Allowed transitions: {allowed}"
        )


_AUDIT_FIELDS = ("timestamp", "user", "action", "details", "old_text", "new_text")


def _audit_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """One stored edit-history entry in the audit trail's shape.

    The editor stores `details` as an object; older rows may hold a string. Entries the AI
    chat wrote before it used the editor's format have no timestamp and keep their extras
    (`source_chunk_id`, `source_chunk_ids`) at the top level: those move into `details`.
    """
    out = {k: entry.get(k) for k in _AUDIT_FIELDS}
    out["user"] = str(entry.get("user") or "unknown")
    out["action"] = str(entry.get("action") or "unknown")
    details = out["details"]
    if details is not None and not isinstance(details, dict | str):
        out["details"] = details = {"value": details}
    extras = {k: v for k, v in entry.items() if k not in _AUDIT_FIELDS and k != "chunk_id"}
    if extras:
        if details is None:
            out["details"] = extras
        elif isinstance(details, dict):
            out["details"] = {**extras, **details}
    return out


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ReviewWorkflowService:
    """Manages workspace review lifecycle: submit, approve, reject, ingest."""

    async def submit_for_review(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        *,
        user: str,
    ) -> dict[str, Any]:
        """Submit a workspace for review.

        Validates that:
        - Workspace exists and is in draft status
        - No rejected chunks exist
        - At least one chunk exists

        Transitions workspace to in_review and releases any edit lock.

        Args:
            session: Database session.
            workspace_id: Workspace UUID.
            user: User submitting the workspace.

        Returns:
            Dict with workspace_id, status, message, and chunk counts.

        Raises:
            ReviewWorkflowError: If workspace not found.
            InvalidTransitionError: If not in draft status.
            ChunkValidationError: If rejected chunks exist or no chunks.
        """
        from callobuzz_rag.content_portal.models.workspace import Workspace

        workspace = await session.get(Workspace, workspace_id)
        if workspace is None:
            raise ReviewWorkflowError(f"Workspace {workspace_id} not found")

        _validate_transition(workspace.status, "in_review")

        counts = await self._count_chunks_by_status(session, workspace_id)

        if counts["total"] == 0:
            raise ChunkValidationError(
                "Cannot submit empty workspace — at least one chunk is required"
            )

        if counts["rejected"] > 0:
            raise ChunkValidationError(
                f"Cannot submit workspace with {counts['rejected']} rejected chunk(s). "
                "Please fix or remove rejected chunks first."
            )

        workspace.status = "in_review"
        workspace.locked_by = None
        workspace.locked_at = None
        workspace.updated_at = datetime.now(UTC)
        await session.flush()

        logger.info(
            "Workspace %s submitted for review by '%s' (%d chunks)",
            workspace_id,
            user,
            counts["total"],
        )

        return {
            "workspace_id": workspace_id,
            "status": "in_review",
            "message": "Workspace submitted for review",
            "total_chunks": counts["total"],
            "approved_chunks": counts["approved"],
            "rejected_chunks": counts["rejected"],
            "draft_chunks": counts["draft"],
        }

    async def approve_workspace(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        *,
        user: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Approve a workspace that is in review.

        In maker_checker mode, the approver must be different from the creator.

        Args:
            session: Database session.
            workspace_id: Workspace UUID.
            user: User approving the workspace.
            comment: Optional approval comment.

        Returns:
            Dict with workspace_id, status, message, review_feedback, updated_at.

        Raises:
            ReviewWorkflowError: If workspace not found.
            InvalidTransitionError: If not in_review status.
            MakerCheckerViolationError: If same user created and approves in maker_checker mode.
        """
        from callobuzz_rag.content_portal.models.workspace import Workspace

        workspace = await session.get(Workspace, workspace_id)
        if workspace is None:
            raise ReviewWorkflowError(f"Workspace {workspace_id} not found")

        _refuse_review_workspace(workspace, "approve")
        _validate_transition(workspace.status, "approved")

        if workspace.workflow_mode == "maker_checker" and workspace.created_by == user:
            raise MakerCheckerViolationError(
                "In maker-checker mode, the creator cannot approve their own workspace"
            )

        workspace.status = "approved"
        workspace.assigned_reviewer = user
        workspace.review_feedback = comment
        workspace.updated_at = datetime.now(UTC)
        await session.flush()

        logger.info(
            "Workspace %s approved by '%s'",
            workspace_id,
            user,
        )

        return {
            "workspace_id": workspace_id,
            "status": "approved",
            "message": "Workspace approved",
            "review_feedback": comment,
            "updated_at": workspace.updated_at,
        }

    async def reject_workspace(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        *,
        user: str,
        feedback: str,
    ) -> dict[str, Any]:
        """Reject a workspace and return it to draft with feedback.

        Args:
            session: Database session.
            workspace_id: Workspace UUID.
            user: User rejecting the workspace.
            feedback: Rejection feedback for the content creator.

        Returns:
            Dict with workspace_id, status, message, review_feedback, updated_at.

        Raises:
            ReviewWorkflowError: If workspace not found.
            InvalidTransitionError: If not in_review status.
        """
        from callobuzz_rag.content_portal.models.workspace import Workspace

        workspace = await session.get(Workspace, workspace_id)
        if workspace is None:
            raise ReviewWorkflowError(f"Workspace {workspace_id} not found")

        _validate_transition(workspace.status, "draft")

        workspace.status = "draft"
        workspace.assigned_reviewer = user
        workspace.review_feedback = feedback
        workspace.updated_at = datetime.now(UTC)
        await session.flush()

        logger.info(
            "Workspace %s rejected by '%s': %s",
            workspace_id,
            user,
            feedback[:100],
        )

        return {
            "workspace_id": workspace_id,
            "status": "draft",
            "message": "Workspace rejected — returned to draft",
            "review_feedback": feedback,
            "updated_at": workspace.updated_at,
        }

    async def ingest_workspace(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        *,
        user: str,
        settings: "Settings | None" = None,
    ) -> dict[str, Any]:
        """Ingest an approved workspace into the RAG engine.

        Packages non-rejected chunks as markdown, creates an IngestJob (flushed) and then
        uploads the file. The ingestion task's arguments come back in ``dispatch``: the
        caller queues it only after committing, so the worker finds the job row.

        Args:
            session: Database session.
            workspace_id: Workspace UUID.
            user: User triggering ingestion.

        Returns:
            Dict with workspace_id, status, message, chunks_ingested, job_id, dispatch.

        Raises:
            ReviewWorkflowError: If workspace not found or no chunks to ingest.
            InvalidTransitionError: If not in approved status.
        """
        from callobuzz_rag.content_portal.models.staged_chunk import (
            StagedChunk,
            StagedChunkStatus,
        )
        from callobuzz_rag.content_portal.models.workspace import Workspace
        from callobuzz_rag.core.config import Settings
        from callobuzz_rag.db.models.corpus import Corpus
        from callobuzz_rag.db.models.ingest_job import IngestJob
        from callobuzz_rag.storage.factory import object_storage
        from callobuzz_rag.storage.key_builder import build_raw_document_key

        workspace = await session.get(Workspace, workspace_id)
        if workspace is None:
            raise ReviewWorkflowError(f"Workspace {workspace_id} not found")

        _refuse_review_workspace(workspace, "ingest")
        _validate_transition(workspace.status, "ingested")

        # Fetch non-rejected chunks
        stmt = (
            select(StagedChunk)
            .where(
                StagedChunk.workspace_id == workspace_id,
                StagedChunk.status != StagedChunkStatus.REJECTED,
            )
            .order_by(StagedChunk.chunk_index)
        )
        result = await session.execute(stmt)
        chunks = list(result.scalars().all())

        if not chunks:
            raise ChunkValidationError("No chunks to ingest — all chunks are rejected")

        # Package as markdown
        markdown_content = "\n\n---\n\n".join(c.text for c in chunks)
        content_bytes = markdown_content.encode("utf-8")

        # Get corpus for qdrant_collection
        corpus = await session.get(Corpus, workspace.corpus_id)
        qdrant_collection = corpus.qdrant_collection if corpus else "chunks"

        # Generate document ID and storage key
        doc_id = uuid.uuid4()
        storage_key = build_raw_document_key(workspace.corpus_id, doc_id)
        settings = settings or Settings()

        # PIP3 uploads: the packaged document is what lands in the business's storage, so it
        # is one upload of today and its bytes count toward the storage limit, like a file
        # sent to POST /ingest (staged text and files are not stored documents yet). Under the
        # tenant lock the route's commit releases; refused before any file is stored.
        if corpus is not None:
            from callobuzz_rag.accounts.caps import check_upload_room

            await check_upload_room(
                session,
                corpus.tenant_id,
                len(content_bytes),
                enabled=bool(getattr(settings, "fair_use_enabled", True)),
            )

        # The job row first (flushed), then the file: the row's foreign key share-locks
        # the corpus, so a concurrent corpus delete waits and then removes the file too.
        file_name = f"portal-{workspace.title[:50]}.md"
        job = IngestJob(
            corpus_id=workspace.corpus_id,
            status="queued",
            mode="new",
            storage_key=storage_key,
            # One new document: counted against the tenant's document limit while queued
            # (the route's check_document_room holds the tenant lock until the commit).
            pending_new_documents=1,
            review_metadata={
                "source": "content_portal",
                "workspace_id": str(workspace_id),
                "workspace_title": workspace.title,
                "chunks_ingested": len(chunks),
                "submitted_by": user,
                # What the storage limit counts while the worker has not written the document.
                "size_bytes": len(content_bytes),
            },
        )
        session.add(job)
        if corpus is not None:  # PIP3 uploads: counted in the job's own transaction
            from callobuzz_rag.fairuse.counters import bump

            await bump(session, corpus.tenant_id, "uploads", 1)
        await session.flush()

        storage = object_storage(settings)
        try:
            await storage.upload(
                bucket=settings.s3_bucket,
                key=storage_key,
                data=io.BytesIO(content_bytes),
                content_type="text/markdown",
            )
        except Exception as e:
            raise IngestStorageError(f"Failed to upload to object storage: {e}") from e

        # Transition workspace
        workspace.status = "ingested"
        workspace.updated_at = datetime.now(UTC)
        await session.flush()

        logger.info(
            "Workspace %s ingested by '%s' — %d chunks, job %s",
            workspace_id,
            user,
            len(chunks),
            job.id,
        )

        return {
            "workspace_id": workspace_id,
            "status": "ingested",
            "message": f"Ingestion started — {len(chunks)} chunks queued",
            "chunks_ingested": len(chunks),
            "job_id": job.id,
            # The caller queues the task only AFTER it commits: a worker must find the
            # job row. (Queued here, a fast worker made its own row with the same id and
            # the request's commit collided with it.)
            "dispatch": {
                "storage_key": storage_key,
                "file_name": file_name,
                "mime_type": "text/markdown",
                "corpus_id": str(workspace.corpus_id),
                "job_id": str(job.id),
                "qdrant_collection": qdrant_collection,
                "mode": "new",
                "target_document_id": None,
                "auto_confirm": True,
            },
        }

    async def get_audit_trail(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], int]:
        """Get the aggregated audit trail for a workspace.

        Collects edit_history entries from all staged chunks, sorted newest first.

        Args:
            session: Database session.
            workspace_id: Workspace UUID.
            offset: Pagination offset.
            limit: Pagination limit.

        Returns:
            Tuple of (entries list, total count).

        Raises:
            ReviewWorkflowError: If workspace not found.
        """
        from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk
        from callobuzz_rag.content_portal.models.workspace import Workspace

        workspace = await session.get(Workspace, workspace_id)
        if workspace is None:
            raise ReviewWorkflowError(f"Workspace {workspace_id} not found")

        stmt = select(StagedChunk).where(StagedChunk.workspace_id == workspace_id)
        result = await session.execute(stmt)
        chunks = list(result.scalars().all())

        # Aggregate all edit_history entries with chunk_id
        all_entries: list[dict[str, Any]] = []
        for chunk in chunks:
            if chunk.edit_history:
                for entry in chunk.edit_history:
                    if isinstance(entry, dict):
                        all_entries.append({**_audit_entry(entry), "chunk_id": chunk.id})

        # Sort newest first by timestamp (entries without one last)
        all_entries.sort(
            key=lambda e: str(e.get("timestamp") or ""),
            reverse=True,
        )

        total = len(all_entries)
        paginated = all_entries[offset : offset + limit]

        return paginated, total

    async def _count_chunks_by_status(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
    ) -> dict[str, int]:
        """Count chunks grouped by status.

        Args:
            session: Database session.
            workspace_id: Workspace UUID.

        Returns:
            Dict with keys: total, draft, approved, rejected.
        """
        from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk

        stmt = (
            select(StagedChunk.status, func.count(StagedChunk.id))
            .where(StagedChunk.workspace_id == workspace_id)
            .group_by(StagedChunk.status)
        )
        result = await session.execute(stmt)
        rows = result.all()

        counts: dict[str, int] = {"total": 0, "draft": 0, "approved": 0, "rejected": 0}
        for status, count in rows:
            counts[status] = count
            counts["total"] += count

        return counts
