"""Post-ingestion review: review an ingested document's chunks in a portal workspace,
then publish the result as a new version of that document.

Creating a review copies the chunks of the document's ACTIVE version into a new
workspace of kind ``post_ingest_review``; the workspace records that version in
``reviewed_version_id`` (a column, not editable through any route).

Finalizing never edits the reviewed version's chunks in place. It builds a NEW
``DocumentVersion`` from the workspace's accepted staged chunks and swaps it in the way
re-ingestion and version restore do: the new version becomes active, the reviewed one
stays in Postgres as an inactive version (restorable with
``POST /api/v1/documents/{id}/versions/{version}/restore``), and the reviewed version's
points leave the corpus's collection.

What goes into the new version (`plan_new_version`):

- every staged chunk that is not rejected — unchanged, edited, added, split halves and
  merged chunks — in the workspace's order; rejected and deleted staged chunks are left
  out;
- parent chunks re-derived by the chunker's rule (``StructureAwareChunker._add_parent_child``):
  a parent survives only while its section still has more than one child; its text is
  its surviving children's text (tables and code left out) joined by blank lines; it is
  dropped when that text is empty or longer than twice ``chunk_max_tokens``. A parent the
  reviewer rejected or deleted is dropped and its children stand alone. An added chunk
  joins a parent when the chunker would have grouped it there (the parent's heading
  path, next to that parent's chunks). Parents are derived text: any staged chunk that
  descends from a parent (its copy, or on older reviews an edit, split half or merge that
  kept its metadata) only keeps the parent alive, and a text of its own is reported in
  the response (`discarded_parent_edits`) instead of published. The editor refuses to
  change a parent copy's text in the first place (`parent_chunk_derived`);
- every chunk gets fresh token counts, enrichment (summary, keywords, language), index
  text, dense and BM25 vectors and payload, built as ingest builds them
  (``chunking/index_text.py``, ``pipeline/payload.py``), with the corpus's own embedder,
  into the corpus's own collection — and, during an embedding migration, into the
  migration's new collection as ingest's dual-write does.

A staged chunk's ``engine_chunk_id`` (in its metadata, which the editor lets anyone
change) is only used to look up the chunk it came from INSIDE the reviewed version, for
attributes (heading path, page, table/code flags, its parent). It never selects a chunk
to write to or delete: nothing outside the new version is ever written.

Order of work (so a failure never leaves the document half-swapped): build everything
first with no side effects (enrich and embed), then the Postgres changes (flushed, not
committed), then Qdrant (new points in, old points out), then commit. A failure after the
first Qdrant write removes the new points again, restores the old ones if they had been
removed, rolls back and raises `FinalizeFailedError` (HTTP 503).
"""

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from callobuzz_rag.core.exceptions import ContentPortalError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.chunking.chunker import StructureAwareChunker
    from callobuzz_rag.chunking.token_counter import TokenCounter
    from callobuzz_rag.content_portal.models.workspace import Workspace
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.db.models.chunk import Chunk
    from callobuzz_rag.db.models.document import Document
    from callobuzz_rag.db.models.document_version import DocumentVersion
    from callobuzz_rag.embeddings.base import EmbeddingProvider, EmbeddingResult
    from callobuzz_rag.enrichment.base import EnrichmentProvider
    from callobuzz_rag.migration.context import DualWriteTarget
    from callobuzz_rag.vectordb.base import VectorPoint, VectorStorageProvider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENGINE_CHUNK_ID_KEY = "engine_chunk_id"
REVIEW_SOURCE_KEY = "review_source"
REVIEW_SOURCE_VALUE = "post_ingest_review"

# edit_history actions that count as a modification
_MODIFICATION_ACTIONS = {"edit_text", "split", "merge", "rewrite"}

# Points per Qdrant request, as ingest batches them.
_UPSERT_BATCH = 256

# How long finalize waits for the workspace and document row locks (an ingest or version
# restore of the same document may hold them) before answering 409 document_busy.
DEFAULT_LOCK_WAIT_SECONDS = 5.0
_LOCK_NOT_AVAILABLE = "55P03"  # PostgreSQL: lock_timeout expired / NOWAIT refused

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PostIngestReviewError(ContentPortalError):
    """Base exception for post-ingest review errors.

    `status_code` is the HTTP status the route answers with; `code`, when set, is the
    machine code of the error body (`api/errors.py`).
    """

    status_code = 400
    code: str | None = None


class DocumentNotFoundError(PostIngestReviewError):
    """The requested document version does not exist."""

    status_code = 404


class WorkspaceNotFoundError(PostIngestReviewError):
    """The workspace does not exist (deleted since the request was scoped)."""

    status_code = 404


class ReviewAlreadyExistsError(PostIngestReviewError):
    """A review workspace already exists for this document version.

    `workspace_id` is that open review; the route returns it in the error body.
    """

    status_code = 409
    code = "review_exists"

    def __init__(self, message: str, *, workspace_id: uuid.UUID | None = None) -> None:
        super().__init__(message)
        self.workspace_id = workspace_id


class NoChunksToReviewError(PostIngestReviewError):
    """The document version has no chunks to review."""

    status_code = 422


class VersionNotActiveError(PostIngestReviewError):
    """The document version is not (or no longer) the document's active version."""

    status_code = 409
    code = "version_not_active"


class ReviewedDocumentDeletedError(PostIngestReviewError):
    """The document the workspace reviews was deleted; there is nothing to publish to."""

    status_code = 409
    code = "reviewed_document_deleted"


class WrongWorkspaceKindError(PostIngestReviewError):
    """The workspace is not a post-ingest review workspace."""

    status_code = 409
    code = "wrong_workspace_kind"


class WorkspaceNotInReviewError(PostIngestReviewError):
    """The review workspace is not in review (already finalized, or archived)."""

    status_code = 409
    code = "invalid_status_transition"


class ReviewLockedError(PostIngestReviewError):
    """Someone else holds the workspace's edit lock."""

    status_code = 409
    code = "workspace_locked"


class FinalizeBusyError(PostIngestReviewError):
    """The workspace or the reviewed document is locked by another operation."""

    status_code = 409
    code = "document_busy"


class ReviewChangedError(PostIngestReviewError):
    """The staged chunks changed while the new version was being built."""

    status_code = 409
    code = "review_changed"


class NothingToFinalizeError(PostIngestReviewError):
    """Every staged chunk was rejected or deleted: the new version would be empty."""

    status_code = 422
    code = "nothing_to_finalize"


class FinalizeFailedError(PostIngestReviewError):
    """Embedding or the vector store failed; nothing was changed (or it was undone)."""

    status_code = 503
    code = "finalize_failed"

    def __init__(self, message: str, *, stage: str) -> None:
        super().__init__(message)
        self.stage = stage


# ---------------------------------------------------------------------------
# The plan: which chunks the new version holds
# ---------------------------------------------------------------------------


@dataclass
class PlannedChunk:
    """One chunk of the new version."""

    id: uuid.UUID
    text: str
    structure_path: list[str] | None
    origin: Any | None  # the reviewed version's chunk it comes from, if any
    unchanged: bool  # same text as `origin`
    is_table: bool
    is_code: bool
    is_parent: bool = False
    parent_index: int | None = None  # position of its parent in the plan


@dataclass
class DiscardedParentEdit:
    """A staged chunk descending from a parent whose own text was not published.

    Parent text is derived from the surviving children, so a parent's staged copy only
    keeps the parent alive. When such a copy's text differs from the parent's (it was
    edited, rewritten, split or merged, or its metadata was pointed at a parent), that
    text is dropped, and reported here instead of silently.
    """

    staged_chunk_id: uuid.UUID
    parent_chunk_id: uuid.UUID
    reason: str  # the last text-changing action in its edit history, else "edited"


@dataclass
class ReviewPlan:
    """The new version's chunks and what happened to the reviewed version's."""

    chunks: list[PlannedChunk]
    staged_total: int
    rejected: int
    unchanged: int
    edited: int
    added: int
    parents: int
    original_total: int
    original_dropped: int
    discarded: list[DiscardedParentEdit] = field(default_factory=list)


def _flag(meta: dict[str, Any] | None, name: str) -> bool:
    value = (meta or {}).get(name)
    return value if isinstance(value, bool) else False


def _meta_path(meta: dict[str, Any] | None) -> list[str] | None:
    path = (meta or {}).get("structure_path")
    if isinstance(path, list) and all(isinstance(p, str) for p in path):
        return list(path)
    return None


def parent_links(original: list[Any]) -> dict[uuid.UUID, uuid.UUID]:
    """child id -> parent id for a version's chunks.

    Stored `parent_chunk_id`s when the version has any. Ingest has never actually
    stored them (it linked ids the database had not assigned yet), so for such a
    version the links are recovered by the chunker's own rule
    (`StructureAwareChunker._add_parent_child`): a parent comes right before its
    section's other chunks, which share its heading path, there are at least two of
    them, and its text is exactly theirs (tables and code left out) joined by blank
    lines.
    """
    ids = {c.id for c in original}
    stored = {
        c.id: c.parent_chunk_id
        for c in original
        if c.parent_chunk_id is not None and c.parent_chunk_id in ids
    }
    if stored:
        return stored
    ordered = sorted(original, key=lambda c: (c.chunk_index or 0, str(c.id)))
    links: dict[uuid.UUID, uuid.UUID] = {}
    for i, candidate in enumerate(ordered):
        if candidate.id in links:
            continue
        section = []
        for chunk in ordered[i + 1 :]:
            if list(chunk.structure_path or []) != list(candidate.structure_path or []):
                break
            section.append(chunk)
        if len(section) < 2:
            continue
        text = "\n\n".join(c.text for c in section if not c.is_table and not c.is_code)
        if text.strip() and text == candidate.text:
            links.update({c.id: candidate.id for c in section})
    return links


def plan_new_version(
    original: "list[Chunk]",
    staged: list[Any],
    *,
    counter: "TokenCounter",
    max_parent_tokens: int,
) -> ReviewPlan:
    """The chunks of the version a review produces. Pure: no I/O.

    Args:
        original: The reviewed version's chunks.
        staged: Every staged chunk of the workspace.
        counter: Token counter of the corpus's chunker.
        max_parent_tokens: A parent longer than this is dropped (the chunker's 2x max).
    """
    from callobuzz_rag.content_portal.models.staged_chunk import StagedChunkStatus

    by_id = {c.id: c for c in original}
    parent_of = parent_links(original)
    parent_ids = set(parent_of.values())
    ordered = sorted(staged, key=lambda s: (s.chunk_index, str(s.id)))
    accepted = [s for s in ordered if s.status != StagedChunkStatus.REJECTED]

    # A staged chunk descending from a parent (its copy, and on reviews made before the
    # editor refused it, an edit, split half or merge that kept its metadata) is a
    # marker: it keeps the parent alive, its text is never published (the parent's
    # text is derived, and publishing a split half would repeat the children's text).
    kept_parents: set[uuid.UUID] = set()
    discarded: list[DiscardedParentEdit] = []
    leaves: list[tuple[Any, Any | None]] = []
    for s in accepted:
        engine_id = _engine_chunk_id(s)
        origin = by_id.get(engine_id) if engine_id is not None else None
        if origin is not None and origin.id in parent_ids:
            kept_parents.add(origin.id)
            if s.text != origin.text:
                discarded.append(
                    DiscardedParentEdit(
                        staged_chunk_id=s.id, parent_chunk_id=origin.id, reason=_edit_reason(s)
                    )
                )
            continue
        leaves.append((s, origin))

    def leaf_flags(s: Any, origin: Any | None) -> tuple[bool, bool]:
        if origin is not None:
            return bool(origin.is_table), bool(origin.is_code)
        return _flag(s.metadata_, "is_table"), _flag(s.metadata_, "is_code")

    def leaf_path(s: Any, origin: Any | None) -> list[str]:
        if origin is not None:
            return list(origin.structure_path or [])
        return _meta_path(s.metadata_) or []

    # Each leaf's parent: through its origin; an added chunk (no origin) joins a kept
    # parent the way the chunker groups (`_add_parent_child`: consecutive chunks of one
    # heading path) - when it sits next to that parent's chunks and has its path.
    leaf_parent: list[uuid.UUID | None] = []
    for _s, origin in leaves:
        parent = parent_of.get(origin.id) if origin is not None else None
        leaf_parent.append(parent if parent in kept_parents else None)
    paths = [leaf_path(s, origin) for s, origin in leaves]
    for order in (range(len(leaves)), range(len(leaves) - 1, -1, -1)):
        step = 1 if order.step > 0 else -1
        for i in order:
            if leaves[i][1] is not None or leaf_parent[i] is not None:
                continue
            j = i - step  # the neighbour already visited in this pass
            neighbour = leaf_parent[j] if 0 <= j < len(leaves) else None
            if (
                neighbour is not None
                and paths[j] == paths[i]
                and list(by_id[neighbour].structure_path or []) == paths[i]
            ):
                leaf_parent[i] = neighbour

    groups: dict[uuid.UUID, list[int]] = {}
    for i, parent in enumerate(leaf_parent):
        if parent is not None:
            groups.setdefault(parent, []).append(i)

    parent_text: dict[uuid.UUID, str] = {}
    for parent, members in groups.items():
        if len(members) < 2:
            continue
        text = "\n\n".join(leaves[i][0].text for i in members if not any(leaf_flags(*leaves[i])))
        if text.strip() and counter.count(text) <= max_parent_tokens:
            parent_text[parent] = text

    chunks: list[PlannedChunk] = []
    placed: dict[uuid.UUID, int] = {}
    unchanged = edited = added = 0
    for (s, origin), parent in zip(leaves, leaf_parent, strict=True):
        parent_index = None
        if parent is not None and parent in parent_text:
            if parent not in placed:
                p = by_id[parent]
                chunks.append(
                    PlannedChunk(
                        id=uuid.uuid4(),
                        text=parent_text[parent],
                        structure_path=list(p.structure_path) if p.structure_path else None,
                        origin=p,
                        unchanged=parent_text[parent] == p.text,
                        is_table=False,
                        is_code=False,
                        is_parent=True,
                    )
                )
                placed[parent] = len(chunks) - 1
            parent_index = placed[parent]
        is_table, is_code = leaf_flags(s, origin)
        same = origin is not None and s.text == origin.text
        if origin is None:
            added += 1
        elif same:
            unchanged += 1
        else:
            edited += 1
        path = origin.structure_path if origin is not None else _meta_path(s.metadata_)
        chunks.append(
            PlannedChunk(
                id=uuid.uuid4(),
                text=s.text,
                structure_path=list(path) if path else None,
                origin=origin,
                unchanged=same,
                is_table=is_table,
                is_code=is_code,
                parent_index=parent_index,
            )
        )

    carried = {c.origin.id for c in chunks if c.origin is not None}
    return ReviewPlan(
        chunks=chunks,
        staged_total=len(ordered),
        rejected=len(ordered) - len(accepted),
        unchanged=unchanged,
        edited=edited,
        added=added,
        parents=len(placed),
        original_total=len(original),
        original_dropped=len(set(by_id) - carried),
        discarded=discarded,
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass
class _Built:
    """Everything the new version needs, computed before anything is written."""

    rows: "list[Chunk]"
    metadata: list[Any]
    embeddings: list[Any]
    points: "list[VectorPoint]"
    dual_points: "list[VectorPoint] | None"
    dual_embeddings: list[Any]
    vectors_by_text: dict[str, list[float]]


class PostIngestReviewService:
    """Manages post-ingestion review: create workspace, finalize, status."""

    def __init__(
        self,
        vector_store: "VectorStorageProvider",
        embedder: "EmbeddingProvider",
        *,
        enricher: "EnrichmentProvider | None" = None,
        chunker: "StructureAwareChunker | None" = None,
        settings: "Settings | None" = None,
    ) -> None:
        from callobuzz_rag.chunking.chunker import StructureAwareChunker
        from callobuzz_rag.enrichment.extractive import ExtractiveEnrichmentProvider

        self._vector_store = vector_store
        self._embedder = embedder
        self._enricher = enricher or ExtractiveEnrichmentProvider()
        self._chunker = chunker or StructureAwareChunker()
        self._settings = settings

    # -- create ---------------------------------------------------------------

    async def create_review_workspace(
        self,
        session: "AsyncSession",
        document_version_id: uuid.UUID,
        corpus_id: uuid.UUID,
        *,
        user: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Create a review workspace by copying the chunks of a document's active version.

        Raises:
            DocumentNotFoundError: The document version does not exist.
            VersionNotActiveError: It is not the document's active version.
            ReviewAlreadyExistsError: A review workspace already exists for it.
            NoChunksToReviewError: It has no chunks.
        """
        from sqlalchemy import select

        from callobuzz_rag.content_portal.models.staged_chunk import (
            StagedChunk,
            StagedChunkSource,
            StagedChunkStatus,
        )
        from callobuzz_rag.content_portal.models.workspace import (
            Workspace,
            WorkspaceKind,
            WorkspaceStatus,
        )
        from callobuzz_rag.db.models.chunk import Chunk
        from callobuzz_rag.db.models.document_version import DocumentVersion

        doc_version = await session.get(DocumentVersion, document_version_id)
        if doc_version is None:
            raise DocumentNotFoundError(f"Document version {document_version_id} not found")
        if doc_version.status != "active":
            raise VersionNotActiveError(
                f"Document version {document_version_id} is not the document's active "
                f"version (status={doc_version.status}); review the active version instead"
            )

        # An open review of this version blocks a second one. A finalized review
        # (INGESTED, with a finalized version) does not: when its version is restored,
        # that version can be reviewed again.
        existing = await session.execute(
            select(Workspace.id).where(
                Workspace.reviewed_version_id == document_version_id,
                Workspace.status.not_in([WorkspaceStatus.ARCHIVED, WorkspaceStatus.INGESTED]),
                Workspace.finalized_version_id.is_(None),
            )
        )
        open_review = existing.first()
        if open_review is not None:
            raise ReviewAlreadyExistsError(
                f"A review workspace already exists for document version {document_version_id}",
                workspace_id=open_review[0],
            )

        chunk_result = await session.execute(
            select(Chunk)
            .where(
                Chunk.document_version_id == document_version_id,
                Chunk.corpus_id == corpus_id,
            )
            .order_by(Chunk.chunk_index)
        )
        engine_chunks = list(chunk_result.scalars().all())
        if not engine_chunks:
            raise NoChunksToReviewError(
                f"Document version {document_version_id} has no chunks to review"
            )

        ws_title = title or f"Post-ingest review: {document_version_id}"
        workspace = Workspace(
            id=uuid.uuid4(),
            corpus_id=corpus_id,
            title=ws_title,
            kind=WorkspaceKind.POST_INGEST_REVIEW,
            reviewed_version_id=document_version_id,
            status=WorkspaceStatus.IN_REVIEW,
            created_by=user,
        )
        session.add(workspace)
        await session.flush()

        for chunk in engine_chunks:
            session.add(
                StagedChunk(
                    id=uuid.uuid4(),
                    workspace_id=workspace.id,
                    text=chunk.text,
                    chunk_index=chunk.chunk_index or 0,
                    status=StagedChunkStatus.APPROVED,
                    source=StagedChunkSource.FROM_INGESTION,
                    # Informational (and the link back to the chunk it was copied from);
                    # the version under review is the workspace's own column.
                    metadata_={
                        ENGINE_CHUNK_ID_KEY: str(chunk.id),
                        REVIEW_SOURCE_KEY: REVIEW_SOURCE_VALUE,
                        "document_version_id": str(document_version_id),
                        "original_chunk_index": chunk.chunk_index,
                        "is_table": chunk.is_table,
                        "is_code": chunk.is_code,
                        "structure_path": chunk.structure_path,
                        "parent_chunk_id": (
                            str(chunk.parent_chunk_id) if chunk.parent_chunk_id else None
                        ),
                    },
                )
            )
        await session.flush()

        logger.info(
            "Created post-ingest review workspace %s for doc version %s (%d chunks copied)",
            workspace.id,
            document_version_id,
            len(engine_chunks),
        )
        return {
            "workspace_id": workspace.id,
            "document_version_id": document_version_id,
            "corpus_id": corpus_id,
            "title": ws_title,
            "status": WorkspaceStatus.IN_REVIEW,
            "chunks_copied": len(engine_chunks),
            "message": f"Review workspace created with {len(engine_chunks)} chunks",
        }

    # -- finalize -------------------------------------------------------------

    async def finalize_review(
        self,
        session: "AsyncSession",
        workspace_id: uuid.UUID,
        *,
        user: str,
        collection_name: str,
        lock_timeout_minutes: int = 30,
        lock_wait_seconds: float = DEFAULT_LOCK_WAIT_SECONDS,
    ) -> dict[str, Any]:
        """Publish the review as a new version of the reviewed document, and COMMIT.

        See the module docstring for what the version holds and the order of work.
        `lock_timeout_minutes` is when a portal edit lock goes stale; `lock_wait_seconds`
        how long to wait for the workspace and document rows (an ingest or restore of
        the same document may hold them) before answering `FinalizeBusyError`.

        Raises:
            FinalizeBusyError: The workspace or the document is locked by another
                operation (409); nothing was changed.
            ReviewChangedError: Staged chunks changed while the version was built (409).
            WrongWorkspaceKindError: Not a post-ingest review workspace (409).
            WorkspaceNotInReviewError: Already finalized or archived (409).
            ReviewLockedError: Another user holds a fresh edit lock (409).
            VersionNotActiveError: The document changed underneath (409).
            ReviewedDocumentDeletedError: The reviewed document was deleted (409).
            NothingToFinalizeError: Every staged chunk was rejected or deleted (422).
            FinalizeFailedError: Embedding or the vector store failed (503); undone.
        """
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk
        from callobuzz_rag.content_portal.models.workspace import (
            WorkspaceStatus,
        )
        from callobuzz_rag.db.models.chunk import Chunk

        # 0. Read and check, WITHOUT row locks: the build below (enrichment, embedding)
        # can take long, and must not hold the document or the workspace meanwhile.
        workspace = await self._workspace(session, workspace_id, lock=False)
        _check_workspace(workspace, user, lock_timeout_minutes)
        version, document = await self._active_version(session, workspace, lock=False)

        original = list(
            (
                await session.execute(
                    select(Chunk)
                    .where(
                        Chunk.document_version_id == version.id,
                        Chunk.corpus_id == workspace.corpus_id,
                    )
                    .options(selectinload(Chunk.metadata_record))
                    .order_by(Chunk.chunk_index)
                )
            )
            .scalars()
            .all()
        )
        staged = list(
            (
                await session.execute(
                    select(StagedChunk).where(StagedChunk.workspace_id == workspace_id)
                )
            )
            .scalars()
            .all()
        )
        plan = plan_new_version(
            original,
            staged,
            counter=self._chunker.counter,
            max_parent_tokens=self._chunker.config.max_tokens * 2,
        )
        if not plan.chunks:
            raise NothingToFinalizeError(
                "Every chunk was rejected or deleted, so the new version would be empty. "
                "Keep at least one chunk, or delete the document instead."
            )

        corpus_id = workspace.corpus_id
        document_id, previous_version_id = document.id, version.id
        new_version_id = uuid.uuid4()
        old_points = _old_point_specs(original, corpus_id, document)
        signature = _staged_signature(staged)
        dual = await self._dual_write_target(session, corpus_id)
        try:
            # 1. Build: enrich + embed, no side effects, no locks held.
            built = await self._build(plan, new_version_id, corpus_id, document, dual)

            # 2. Lock the workspace and the version (waiting at most `lock_wait_seconds`),
            # then re-check everything the build relied on.
            workspace, version, document = await self._lock_and_recheck(
                session,
                workspace_id,
                user=user,
                lock_timeout_minutes=lock_timeout_minutes,
                lock_wait_seconds=lock_wait_seconds,
                expected_version_id=previous_version_id,
                signature=signature,
            )

            # 3. Postgres, flushed not committed.
            await self._write_rows(session, workspace, version, document, new_version_id, built)

            # 4. Qdrant, then 5. commit.
            await self._swap_points(
                session,
                collection_name,
                built,
                dual,
                old_version_id=previous_version_id,
                new_version_id=new_version_id,
                old_points=old_points,
                corpus_id=corpus_id,
            )
        finally:
            if dual is not None:
                await dual.embedder.close()

        message = (
            f"Review finalized as a new version: {len(plan.chunks)} chunks "
            f"({plan.unchanged} unchanged, {plan.edited} edited, {plan.added} added, "
            f"{plan.parents} parent{'s' if plan.parents != 1 else ''} re-derived); "
            f"{plan.rejected} rejected in review; {plan.original_dropped} of the previous "
            f"version's {plan.original_total} chunks not carried over"
        )
        if plan.discarded:
            message += (
                f"; {len(plan.discarded)} edit{'s' if len(plan.discarded) != 1 else ''} to "
                "parent chunks discarded (a parent's text is rebuilt from its children)"
            )
        logger.info(
            "Finalized post-ingest review %s by '%s': document %s version %s -> %s. %s",
            workspace_id,
            user,
            document_id,
            previous_version_id,
            new_version_id,
            message,
        )
        return {
            "workspace_id": workspace_id,
            "status": WorkspaceStatus.INGESTED,
            "message": message,
            "document_id": document_id,
            "previous_version_id": previous_version_id,
            "new_version_id": new_version_id,
            "chunks_in_new_version": len(plan.chunks),
            "chunks_unchanged": plan.unchanged,
            "chunks_edited": plan.edited,
            "chunks_added": plan.added,
            "parent_chunks": plan.parents,
            "staged_chunks_rejected": plan.rejected,
            "previous_chunks_dropped": plan.original_dropped,
            "staged_chunks_total": plan.staged_total,
            "discarded_parent_edits": [
                {
                    "staged_chunk_id": d.staged_chunk_id,
                    "parent_chunk_id": d.parent_chunk_id,
                    "reason": d.reason,
                }
                for d in plan.discarded
            ],
        }

    async def _write_rows(
        self,
        session: "AsyncSession",
        workspace: "Workspace",
        version: "DocumentVersion",
        document: "Document",
        new_version_id: uuid.UUID,
        built: _Built,
    ) -> None:
        """The new version (active), its chunks, the old version inactive; flushed only."""
        from sqlalchemy.exc import SQLAlchemyError

        from callobuzz_rag.content_portal.models.workspace import WorkspaceStatus
        from callobuzz_rag.db.models.document_version import DocumentVersion

        try:
            session.add(
                DocumentVersion(
                    id=new_version_id,
                    document_id=document.id,
                    parser_version=version.parser_version,
                    chunk_schema_version=version.chunk_schema_version,
                    status="active",
                )
            )
            version.status = "inactive"
            await session.flush()
            # Parents come before their children in `rows`, so one flush satisfies the
            # self-referencing parent_chunk_id foreign key.
            session.add_all(built.rows)
            await session.flush()
            session.add_all(built.metadata)
            session.add_all(built.embeddings)
            workspace.status = WorkspaceStatus.INGESTED
            workspace.finalized_version_id = new_version_id
            workspace.locked_by = None
            workspace.locked_at = None
            await session.flush()
        except SQLAlchemyError as e:
            logger.error("Writing a finalized review's version failed: %s", e, exc_info=True)
            await session.rollback()
            raise FinalizeFailedError(
                "The database failed while publishing the review; nothing was changed. Try again.",
                stage="database",
            ) from e

    async def _workspace(
        self, session: "AsyncSession", workspace_id: uuid.UUID, *, lock: bool
    ) -> "Workspace":
        """The workspace re-read from the database; with `lock`, row-locked (FOR UPDATE).

        `populate_existing` matters: the route already loaded the row for scoping, and a
        second finalize waiting on this lock must see the first one's committed status.
        """
        from sqlalchemy import select

        from callobuzz_rag.content_portal.models.workspace import Workspace

        query = select(Workspace).where(Workspace.id == workspace_id)
        if lock:
            query = query.with_for_update()
        workspace = (
            await session.execute(query.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        if workspace is None:
            raise WorkspaceNotFoundError(f"Workspace {workspace_id} not found")
        return workspace

    async def _lock_and_recheck(
        self,
        session: "AsyncSession",
        workspace_id: uuid.UUID,
        *,
        user: str,
        lock_timeout_minutes: int,
        lock_wait_seconds: float,
        expected_version_id: uuid.UUID,
        signature: tuple[Any, ...],
    ) -> "tuple[Workspace, DocumentVersion, Document]":
        """Row-lock the workspace, then the version and its document, waiting at most
        `lock_wait_seconds`; re-check what the build relied on under the locks.

        Raises:
            FinalizeBusyError: A lock was not granted in time (409); rolled back.
            ReviewChangedError: The staged chunks are not the ones that were built (409).
            (and every refusal of the unlocked checks, re-made here)
        """
        from sqlalchemy import select, text
        from sqlalchemy.exc import DBAPIError

        from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk

        wait_ms = max(1, int(lock_wait_seconds * 1000))
        try:
            # SET LOCAL lasts until the transaction ends; reset right after the locks.
            await session.execute(text(f"SET LOCAL lock_timeout = {wait_ms}"))
            workspace = await self._workspace(session, workspace_id, lock=True)
            _check_workspace(workspace, user, lock_timeout_minutes)
            version, document = await self._active_version(session, workspace, lock=True)
            await session.execute(text("SET LOCAL lock_timeout = DEFAULT"))
        except DBAPIError as e:
            if getattr(e.orig, "sqlstate", None) != _LOCK_NOT_AVAILABLE:
                raise
            await session.rollback()
            raise FinalizeBusyError(
                "The review's workspace or document is being changed by another operation "
                "(an ingest, a version restore or another finalize); nothing was changed. "
                "Try again shortly."
            ) from e
        if version.id != expected_version_id:
            raise ReviewChangedError(
                "The workspace now reviews another version; nothing was changed"
            )
        staged_now = list(
            (
                await session.execute(
                    select(StagedChunk)
                    .where(StagedChunk.workspace_id == workspace_id)
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        if _staged_signature(staged_now) != signature:
            raise ReviewChangedError(
                "The review's chunks changed while the new version was being built; "
                "nothing was changed. Finalize again to publish the current chunks."
            )
        return workspace, version, document

    async def _active_version(
        self, session: "AsyncSession", workspace: "Workspace", *, lock: bool
    ) -> "tuple[DocumentVersion, Document]":
        """The reviewed version and its document (row-locked with `lock`); it must still
        be the document's active version."""
        from sqlalchemy import select

        from callobuzz_rag.db.models.document import Document
        from callobuzz_rag.db.models.document_version import DocumentVersion

        version_id = workspace.reviewed_version_id
        if version_id is None:  # the FK is ON DELETE SET NULL: the document was deleted
            raise ReviewedDocumentDeletedError(
                "The document this workspace reviews was deleted; nothing to finalize"
            )
        query = (
            select(DocumentVersion, Document)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(DocumentVersion.id == version_id, Document.corpus_id == workspace.corpus_id)
        )
        if lock:
            query = query.with_for_update()
        row = (await session.execute(query.execution_options(populate_existing=True))).first()
        if row is None:
            raise ReviewedDocumentDeletedError(
                "The document this workspace reviews was deleted; nothing to finalize"
            )
        version, document = row
        if version.status != "active":
            raise VersionNotActiveError(
                f"The reviewed version {version_id} is no longer the document's active version "
                "(it was re-ingested, restored or replaced since the review began); start a "
                "new review of the current version"
            )
        return version, document

    async def _dual_write_target(
        self, session: "AsyncSession", corpus_id: uuid.UUID
    ) -> "DualWriteTarget | None":
        if self._settings is None:
            return None
        from callobuzz_rag.migration.context import get_migration_context

        try:
            return await get_migration_context(session, corpus_id, self._settings)
        except Exception:
            logger.warning(
                "Dual-write check failed for corpus %s (non-fatal)", corpus_id, exc_info=True
            )
            return None

    async def _build(
        self,
        plan: ReviewPlan,
        version_id: uuid.UUID,
        corpus_id: uuid.UUID,
        document: "Document",
        dual: "DualWriteTarget | None",
    ) -> _Built:
        """Rows, metadata, embeddings and points of the new version. No side effects."""
        from callobuzz_rag.chunking.index_text import index_text
        from callobuzz_rag.chunking.models import ChunkResult
        from callobuzz_rag.db.models.chunk import Chunk
        from callobuzz_rag.db.models.chunk_metadata import ChunkMetadata
        from callobuzz_rag.db.models.embedding import Embedding
        from callobuzz_rag.enrichment.models import EnrichmentResult
        from callobuzz_rag.pipeline.payload import chunk_payload

        counter = self._chunker.counter
        results = [
            ChunkResult(
                text=c.text,
                chunk_index=i,
                structure_path=list(c.structure_path or []),
                page=c.origin.page if c.origin is not None else None,
                length_tokens=counter.count(c.text),
                is_table=c.is_table,
                is_code=c.is_code,
                parent_chunk_index=c.parent_index,
                is_parent=c.is_parent,
            )
            for i, c in enumerate(plan.chunks)
        ]
        try:
            enrichments = await self._enricher.enrich_batch(results)
        except Exception as e:  # as ingest: enrichment is never fatal
            logger.warning("Enrichment failed for a post-ingest review, using defaults: %s", e)
            enrichments = [EnrichmentResult() for _ in results]

        rows: list[Chunk] = []
        metadata: list[ChunkMetadata] = []
        for planned, result, enrichment in zip(plan.chunks, results, enrichments, strict=True):
            origin = planned.origin
            keep = origin if planned.unchanged else None  # attributes only an unedited text keeps
            row = Chunk(
                id=planned.id,
                document_version_id=version_id,
                corpus_id=corpus_id,
                chunk_index=result.chunk_index,
                text=planned.text,
                context_preamble=keep.context_preamble if keep is not None else None,
                structure_path=planned.structure_path,
                start_offset=keep.start_offset if keep is not None else None,
                end_offset=keep.end_offset if keep is not None else None,
                page=result.page,
                language=enrichment.language,
                length_tokens=result.length_tokens,
                is_table=planned.is_table,
                is_code=planned.is_code,
                parent_chunk_id=(
                    plan.chunks[planned.parent_index].id
                    if planned.parent_index is not None
                    else None
                ),
            )
            rows.append(row)
            old_meta = origin.metadata_record if origin is not None else None
            metadata.append(
                ChunkMetadata(
                    chunk_id=planned.id,
                    summary=enrichment.summary,
                    keywords=enrichment.keywords,
                    confidence_score=enrichment.confidence_score,
                    table_json=old_meta.table_json if keep is not None and old_meta else None,
                    hypothetical_questions=(
                        old_meta.hypothetical_questions if keep is not None and old_meta else None
                    ),
                    tags=old_meta.tags if old_meta else None,
                    valid_from=old_meta.valid_from if old_meta else None,
                    valid_until=old_meta.valid_until if old_meta else None,
                )
            )

        texts = [index_text(c.text, c.structure_path) for c in plan.chunks]
        try:
            vectors = await self._embedder.embed_texts(texts)
        except Exception as e:
            logger.error("Embedding failed while finalizing a review: %s", e, exc_info=True)
            raise FinalizeFailedError(
                "The embedding service failed; nothing was changed. Try again.", stage="embed"
            ) from e
        if len(vectors) != len(texts):
            raise FinalizeFailedError(
                f"The embedder returned {len(vectors)} vectors for {len(texts)} chunks; "
                "nothing was changed",
                stage="embed",
            )

        embeddings = [
            Embedding(
                chunk_id=row.id,
                model=emb.model,
                model_version=emb.model_version,
                vector_ref=str(row.id),
                vector_type="text",
                dimensions=emb.dimensions,
            )
            for row, emb in zip(rows, vectors, strict=True)
        ]
        payloads = [
            chunk_payload(row, corpus_id, document.id, document.doc_metadata, meta.keywords)
            for row, meta in zip(rows, metadata, strict=True)
        ]
        points = _points(rows, texts, [v.vector for v in vectors], payloads)

        dual_points: list[VectorPoint] | None = None
        dual_embeddings: list[Any] = []
        if dual is not None:
            # Best effort, as ingest's dual-write: the migration re-copies what it misses.
            try:
                dual_vectors: list[EmbeddingResult] = await dual.embedder.embed_texts(texts)
                dual_points = _points(rows, texts, [v.vector for v in dual_vectors], payloads)
                dual_embeddings = [
                    Embedding(
                        chunk_id=row.id,
                        model=dual.model,
                        model_version=emb.model_version,
                        vector_ref=str(row.id),
                        vector_type="text",
                        dimensions=emb.dimensions,
                    )
                    for row, emb in zip(rows, dual_vectors, strict=True)
                ]
            except Exception as e:
                logger.warning(
                    "Dual-write embedding for %s failed (best-effort): %s", dual.collection, e
                )
                dual_points = None

        return _Built(
            rows=rows,
            metadata=metadata,
            embeddings=embeddings,
            points=points,
            dual_points=dual_points,
            dual_embeddings=dual_embeddings,
            vectors_by_text={t: v.vector for t, v in zip(texts, vectors, strict=True)},
        )

    async def _swap_points(
        self,
        session: "AsyncSession",
        collection: str,
        built: _Built,
        dual: "DualWriteTarget | None",
        *,
        old_version_id: uuid.UUID,
        new_version_id: uuid.UUID,
        old_points: list[tuple[str, str, dict[str, Any]]],
        corpus_id: uuid.UUID,
    ) -> None:
        """New points in, old points out, commit; undo the vector writes on any failure."""
        store = self._vector_store
        old_removed = False
        stage = "vector_store"
        try:
            for start in range(0, len(built.points), _UPSERT_BATCH):
                await store.upsert(collection, built.points[start : start + _UPSERT_BATCH])
            if dual is not None and built.dual_points is not None:
                try:
                    for start in range(0, len(built.dual_points), _UPSERT_BATCH):
                        await store.upsert(
                            dual.collection, built.dual_points[start : start + _UPSERT_BATCH]
                        )
                    session.add_all(built.dual_embeddings)
                except Exception as e:
                    logger.warning("Dual-write to %s failed (best-effort): %s", dual.collection, e)
            old_removed = True
            await store.delete_by_filter(collection, {"document_version_id": str(old_version_id)})
            stage = "database"
            await session.commit()
        except Exception as e:
            if stage == "database" and await _version_committed(session, new_version_id):
                # The COMMIT reached the database but its answer was lost: the new version
                # is live, so its points must stay (undoing would leave it unsearchable).
                logger.warning(
                    "Commit of review version %s reported %r but it is committed; keeping it",
                    new_version_id,
                    e,
                )
                try:
                    await session.rollback()
                except Exception:
                    logger.debug("Rollback after an applied commit failed", exc_info=True)
            else:
                await self._fail_swap(
                    session,
                    collection,
                    built,
                    dual,
                    e,
                    stage=stage,
                    old_removed=old_removed,
                    new_version_id=new_version_id,
                    old_points=old_points,
                    corpus_id=corpus_id,
                )

        # After the commit: the dual-write collection's copy of the old version (best
        # effort, like everything dual-write does).
        if dual is not None:
            try:
                await store.delete_by_filter(
                    dual.collection, {"document_version_id": str(old_version_id)}
                )
            except Exception as e:
                logger.warning(
                    "Old review version left in dual-write collection %s: %s", dual.collection, e
                )

    async def _fail_swap(
        self,
        session: "AsyncSession",
        collection: str,
        built: _Built,
        dual: "DualWriteTarget | None",
        error: Exception,
        *,
        stage: str,
        old_removed: bool,
        new_version_id: uuid.UUID,
        old_points: list[tuple[str, str, dict[str, Any]]],
        corpus_id: uuid.UUID,
    ) -> None:
        """Undo the vector writes, roll back, raise `FinalizeFailedError`."""
        logger.error(
            "Finalizing a review failed at %s; undoing its vector writes: %s",
            stage,
            error,
            exc_info=True,
        )
        await self._undo(
            collection,
            dual,
            new_version_id=new_version_id,
            old_points=old_points if old_removed else [],
            known=built.vectors_by_text,
            corpus_id=corpus_id,
        )
        await session.rollback()
        failed = "vector store" if stage == "vector_store" else "database"
        raise FinalizeFailedError(
            f"The {failed} failed while publishing the review; nothing was changed. Try again.",
            stage=stage,
        ) from error

    async def _undo(
        self,
        collection: str,
        dual: "DualWriteTarget | None",
        *,
        new_version_id: uuid.UUID,
        old_points: list[tuple[str, str, dict[str, Any]]],
        known: dict[str, list[float]],
        corpus_id: uuid.UUID,
    ) -> None:
        """Remove the new version's points; put back the old version's if they were removed."""
        store = self._vector_store
        try:
            await store.delete_by_filter(collection, {"document_version_id": str(new_version_id)})
        except Exception:
            logger.error(
                "Could not remove the points of the unpublished version %s from %s; they are "
                "orphans (search may return them). Remove them with a Qdrant delete by "
                "document_version_id, or rebuild: python -m callobuzz_rag.retrieval.reindex "
                "--apply --rebuild --corpus %s",
                new_version_id,
                collection,
                corpus_id,
                exc_info=True,
            )
        if dual is not None:
            try:
                await store.delete_by_filter(
                    dual.collection, {"document_version_id": str(new_version_id)}
                )
            except Exception:
                logger.warning("Dual-write collection %s keeps orphan points", dual.collection)
        if not old_points:
            return
        try:
            missing = [text for _, text, _ in old_points if text not in known]
            if missing:
                fresh = await self._embedder.embed_texts(missing)
                known = {**known, **{t: v.vector for t, v in zip(missing, fresh, strict=True)}}
            points = _points_from_specs(old_points, known)
            for start in range(0, len(points), _UPSERT_BATCH):
                await store.upsert(collection, points[start : start + _UPSERT_BATCH])
        except Exception:
            logger.error(
                "Could not restore the points of the still-active reviewed version in %s: "
                "part of the document may be missing from search. Rebuild the collection: "
                "python -m callobuzz_rag.retrieval.reindex --apply --rebuild --corpus %s",
                collection,
                corpus_id,
                exc_info=True,
            )

    # -- status ---------------------------------------------------------------

    async def get_review_status(
        self,
        session: "AsyncSession",
        workspace_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Get the status of a post-ingestion review workspace.

        Raises:
            WorkspaceNotFoundError: If workspace not found (404).
        """
        from sqlalchemy import select

        from callobuzz_rag.content_portal.models.staged_chunk import (
            StagedChunk,
            StagedChunkStatus,
        )
        from callobuzz_rag.content_portal.models.workspace import Workspace

        workspace = await session.get(Workspace, workspace_id)
        if workspace is None:
            raise WorkspaceNotFoundError(f"Workspace {workspace_id} not found")

        result = await session.execute(
            select(StagedChunk).where(StagedChunk.workspace_id == workspace_id)
        )
        chunks = list(result.scalars().all())
        return {
            "workspace_id": workspace_id,
            "kind": workspace.kind,
            "document_version_id": workspace.reviewed_version_id,
            "finalized_version_id": workspace.finalized_version_id,
            "corpus_id": workspace.corpus_id,
            "status": workspace.status,
            "total_chunks": len(chunks),
            "approved_chunks": sum(1 for c in chunks if c.status == StagedChunkStatus.APPROVED),
            "rejected_chunks": sum(1 for c in chunks if c.status == StagedChunkStatus.REJECTED),
            "draft_chunks": sum(1 for c in chunks if c.status == StagedChunkStatus.DRAFT),
            "modified_chunks": sum(1 for c in chunks if _was_modified(c)),
        }


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


async def _version_committed(session: "AsyncSession", version_id: uuid.UUID) -> bool:
    """Whether `version_id` is committed and active, read in a FRESH session (the failed
    one cannot be trusted). False when that cannot be checked either."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.db.models.document_version import DocumentVersion

    bind = session.bind
    if bind is None:
        return False
    try:
        async with AsyncSession(bind=bind) as fresh:
            status = (
                await fresh.execute(
                    select(DocumentVersion.status).where(DocumentVersion.id == version_id)
                )
            ).scalar_one_or_none()
    except Exception:
        logger.error(
            "Could not check whether review version %s was committed; undoing its vector "
            "writes. If it WAS committed its document is missing from search: run "
            "python -m callobuzz_rag.retrieval.reindex --apply --rebuild",
            version_id,
            exc_info=True,
        )
        return False
    return status == "active"


def _check_workspace(workspace: Any, user: str, lock_timeout_minutes: int) -> None:
    """A post-ingest review, in review, not edit-locked by someone else."""
    from callobuzz_rag.content_portal.models.workspace import WorkspaceKind, WorkspaceStatus

    if workspace.kind != WorkspaceKind.POST_INGEST_REVIEW:
        raise WrongWorkspaceKindError(
            "finalize-review publishes a review of an ingested document; this workspace "
            f"is a '{workspace.kind}' workspace (use submit / approve / ingest)"
        )
    if workspace.status != WorkspaceStatus.IN_REVIEW:
        raise WorkspaceNotInReviewError(
            f"Workspace {workspace.id} is not in review (status={workspace.status})"
        )
    _check_lock(workspace, user, lock_timeout_minutes)


def _staged_signature(staged: list[Any]) -> tuple[Any, ...]:
    """What the plan depends on in the staged chunks, to notice a change during the build."""
    import json

    return tuple(
        sorted(
            (
                str(s.id),
                s.text,
                str(s.status),
                s.chunk_index,
                json.dumps(s.metadata_, sort_keys=True, default=str),
            )
            for s in staged
        )
    )


def _check_lock(workspace: Any, user: str, lock_timeout_minutes: int) -> None:
    """Refuse when someone else holds a lock that has not gone stale."""
    from datetime import UTC, datetime, timedelta

    holder = workspace.locked_by
    if holder is None or holder == user:
        return
    locked_at = workspace.locked_at
    if (
        locked_at is not None
        and lock_timeout_minutes > 0
        and datetime.now(UTC) >= locked_at + timedelta(minutes=lock_timeout_minutes)
    ):
        return  # stale: anyone may proceed, as with acquiring the lock
    raise ReviewLockedError(
        f"Workspace is locked by '{holder}'; they must unlock it (or let the lock expire) "
        "before the review can be finalized"
    )


def _points(
    rows: list[Any],
    texts: list[str],
    vectors: list[list[float]],
    payloads: list[Any],
) -> "list[VectorPoint]":
    from callobuzz_rag.retrieval.sparse import text_to_sparse_vector
    from callobuzz_rag.vectordb.base import VectorPoint

    points = []
    for row, text, vector, payload in zip(rows, texts, vectors, payloads, strict=True):
        body = payload.to_dict()
        sparse = text_to_sparse_vector(text)
        points.append(
            VectorPoint(
                id=str(row.id),
                vector=vector,
                payload=body,
                sparse_vector=None if sparse.is_empty() else sparse.to_sparse_vector(),
            )
        )
    return points


def _old_point_specs(
    original: list[Any], corpus_id: uuid.UUID, document: Any
) -> list[tuple[str, str, dict[str, Any]]]:
    """(id, index text, payload) of the reviewed version's points, read now while the rows
    are loaded, so they can be put back after a rollback without touching the session."""
    from callobuzz_rag.chunking.index_text import index_text
    from callobuzz_rag.pipeline.payload import chunk_payload

    specs = []
    for chunk in original:
        keywords = chunk.metadata_record.keywords if chunk.metadata_record else None
        payload = chunk_payload(chunk, corpus_id, document.id, document.doc_metadata, keywords)
        specs.append(
            (str(chunk.id), index_text(chunk.text, chunk.structure_path), payload.to_dict())
        )
    return specs


def _points_from_specs(
    specs: list[tuple[str, str, dict[str, Any]]], vectors: dict[str, list[float]]
) -> "list[VectorPoint]":
    from callobuzz_rag.retrieval.sparse import text_to_sparse_vector
    from callobuzz_rag.vectordb.base import VectorPoint

    points = []
    for point_id, text, payload in specs:
        sparse = text_to_sparse_vector(text)
        points.append(
            VectorPoint(
                id=point_id,
                vector=vectors[text],
                payload=payload,
                sparse_vector=None if sparse.is_empty() else sparse.to_sparse_vector(),
            )
        )
    return points


def _engine_chunk_id(chunk: object) -> uuid.UUID | None:
    """The engine chunk a staged chunk was copied from, if its metadata names one."""
    return engine_chunk_id_in(getattr(chunk, "metadata_", None))


def engine_chunk_id_in(metadata: object) -> uuid.UUID | None:
    """The engine chunk id a staged chunk's metadata names, if any (and valid)."""
    raw = metadata.get(ENGINE_CHUNK_ID_KEY) if isinstance(metadata, dict) else None
    try:
        return uuid.UUID(str(raw)) if raw else None
    except ValueError:
        return None


async def review_parent_ids(session: "AsyncSession", workspace_id: uuid.UUID) -> set[uuid.UUID]:
    """The engine ids of the reviewed version's parent chunks, for a post-ingest review
    workspace; empty for any other workspace.

    The chunk editor and the AI chat use it to refuse changing a parent copy's text:
    a parent's text is derived from its children when the review is finalized.
    """
    from sqlalchemy import select

    from callobuzz_rag.content_portal.models.workspace import Workspace, WorkspaceKind
    from callobuzz_rag.db.models.chunk import Chunk

    workspace = await session.get(Workspace, workspace_id)
    if (
        workspace is None
        or workspace.kind != WorkspaceKind.POST_INGEST_REVIEW
        or workspace.reviewed_version_id is None
    ):
        return set()
    rows = (
        await session.execute(
            select(Chunk).where(
                Chunk.document_version_id == workspace.reviewed_version_id,
                Chunk.corpus_id == workspace.corpus_id,
            )
        )
    ).scalars()
    return set(parent_links(list(rows)).values())


PARENT_CHUNK_DERIVED = "parent_chunk_derived"
PARENT_CHUNK_DERIVED_MESSAGE = (
    "This chunk is a parent chunk of the reviewed document: its text is rebuilt from its "
    "children when the review is finalized, so it cannot be edited, split, merged or "
    "re-pointed. Edit its children instead, or reject it to let them stand alone."
)


_TEXT_ACTIONS = ("edit_text", "rewrite", "split", "split_from", "merge", "merged_from")


def _edit_reason(chunk: object) -> str:
    """The last text-changing action in a staged chunk's edit history, else "edited"."""
    history = getattr(chunk, "edit_history", None)
    if isinstance(history, list):
        for entry in reversed(history):
            action = entry.get("action") if isinstance(entry, dict) else None
            if action in _TEXT_ACTIONS:
                return str(action)
    return "edited"


def _was_modified(chunk: object) -> bool:
    """Whether a staged chunk's edit_history has any of {edit_text, split, merge, rewrite}."""
    history = getattr(chunk, "edit_history", None)
    if not history or not isinstance(history, list):
        return False
    return any(
        isinstance(entry, dict) and entry.get("action") in _MODIFICATION_ACTIONS
        for entry in history
    )


# ---------------------------------------------------------------------------
# Standalone hook
# ---------------------------------------------------------------------------


async def maybe_auto_create_review_workspace(
    doc_version_id: uuid.UUID,
    corpus_id: uuid.UUID,
    settings: "Settings",
    vector_store: "VectorStorageProvider",
    embedder: "EmbeddingProvider",
) -> dict[str, Any] | None:
    """Post-ingestion hook: auto-create review workspace if enabled.

    Checks the portal configuration for the ``portal_post_ingest_review`` flag. If
    enabled, creates a new async session and delegates to
    :meth:`PostIngestReviewService.create_review_workspace`.

    Returns:
        Result dict from ``create_review_workspace``, or None if skipped.
    """
    from callobuzz_rag.content_portal.config import resolve_portal_config
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.db.models.corpus import Corpus

    if not getattr(settings, "enable_content_portal", False):
        return None

    # There is no module-level session factory in this project; sessions come
    # from an engine built off Settings.
    engine = create_engine(settings)
    try:
        async with create_session_factory(engine)() as session:
            corpus = await session.get(Corpus, corpus_id)
            if corpus is None:
                logger.warning("Cannot auto-create review: corpus %s not found", corpus_id)
                return None

            corpus_config = corpus.retrieval_config or {}
            portal_cfg = resolve_portal_config(corpus_config, settings)

            if not portal_cfg.get("portal_post_ingest_review", False):
                return None

            svc = PostIngestReviewService(vector_store, embedder, settings=settings)
            try:
                result = await svc.create_review_workspace(
                    session,
                    doc_version_id,
                    corpus_id,
                    user="system",
                    title=f"Auto post-ingest review: {doc_version_id}",
                )
                await session.commit()
                logger.info(
                    "Auto-created post-ingest review workspace %s for doc version %s",
                    result["workspace_id"],
                    doc_version_id,
                )
                return result
            except (
                ReviewAlreadyExistsError,
                NoChunksToReviewError,
                VersionNotActiveError,
            ) as e:
                logger.info(
                    "Skipped auto-create review for doc version %s: %s",
                    doc_version_id,
                    e,
                )
                return None
    finally:
        await engine.dispose()
