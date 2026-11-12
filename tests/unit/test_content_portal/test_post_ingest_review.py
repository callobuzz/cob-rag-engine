"""Unit tests for PostIngestReviewService — create, finalize, status, helpers."""

import itertools
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.chunking.token_counter import TokenCounter
from callobuzz_rag.content_portal.services.post_ingest_review import (
    ENGINE_CHUNK_ID_KEY,
    DocumentNotFoundError,
    FinalizeFailedError,
    NoChunksToReviewError,
    NothingToFinalizeError,
    PostIngestReviewError,
    PostIngestReviewService,
    ReviewAlreadyExistsError,
    ReviewLockedError,
    VersionNotActiveError,
    WorkspaceNotInReviewError,
    WrongWorkspaceKindError,
    _check_lock,
    _was_modified,
    plan_new_version,
)

EXISTING_REVIEW_ID = uuid.uuid4()  # the open review the existing-review check finds

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeDocumentVersion:
    """Minimal document version fake."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    document_id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: str = "active"


@dataclass
class FakeEngineChunk:
    """Minimal engine chunk fake."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    document_version_id: uuid.UUID = field(default_factory=uuid.uuid4)
    text: str = "Engine chunk text"
    chunk_index: int = 0
    is_table: bool = False
    is_code: bool = False
    structure_path: list[str] | None = None
    parent_chunk_id: uuid.UUID | None = None


@dataclass
class FakeWorkspace:
    """Minimal workspace fake."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    corpus_id: uuid.UUID = field(default_factory=uuid.uuid4)
    title: str = "Test Workspace"
    kind: str = "post_ingest_review"
    reviewed_version_id: uuid.UUID | None = field(default_factory=uuid.uuid4)
    finalized_version_id: uuid.UUID | None = None
    status: str = "in_review"
    locked_by: str | None = None
    locked_at: datetime | None = None
    created_by: str = "test_user"
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class FakeStagedChunk:
    """Minimal staged chunk fake."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    text: str = "Staged chunk text"
    chunk_index: int = 0
    status: str = "approved"
    source: str = "from_ingestion"
    metadata_: dict | None = None
    edit_history: list | None = None


@dataclass
class FakeEmbeddingResult:
    """Minimal embedding result fake."""

    vector: list[float] = field(default_factory=lambda: [0.1, 0.2, 0.3])
    model: str = "test-model"
    model_version: str = "1.0"
    dimensions: int = 3
    token_count: int = 10


# ---------------------------------------------------------------------------
# Session builder
# ---------------------------------------------------------------------------


def _make_session(
    workspace: FakeWorkspace | None = None,
    doc_version: FakeDocumentVersion | None = None,
    engine_chunks: list[FakeEngineChunk] | None = None,
    staged_chunks: list[FakeStagedChunk] | None = None,
    existing_review: bool = False,
) -> AsyncMock:
    """Build a mock async session.

    Handles session.get() for Workspace and DocumentVersion, and
    session.execute() for chunk queries and existing-review checks.

    Uses call ordering to distinguish between execute() calls:
    - create_review_workspace: call 1 = existing-review check, call 2 = engine chunks
    - finalize_review / get_review_status: call 1 = staged chunks
    """
    session = AsyncMock()
    engine_chunks = engine_chunks or []
    staged_chunks = staged_chunks or []

    async def fake_get(model_cls, obj_id):
        cls_name = model_cls.__name__ if hasattr(model_cls, "__name__") else str(model_cls)
        if cls_name == "DocumentVersion":
            if doc_version is not None and doc_version.id == obj_id:
                return doc_version
            return None
        if cls_name == "Workspace":
            if workspace is not None and workspace.id == obj_id:
                return workspace
            return None
        return None

    session.get = AsyncMock(side_effect=fake_get)
    session.add = MagicMock()
    session.flush = AsyncMock()

    call_count = {"n": 0}

    async def fake_execute(stmt):
        call_count["n"] += 1
        mock_result = MagicMock()

        # For create_review_workspace flow (doc_version is set):
        # call 1 = existing-review check, call 2 = engine chunks
        if doc_version is not None:
            if call_count["n"] == 1:
                # Existing-review check
                mock_result.first.return_value = (EXISTING_REVIEW_ID,) if existing_review else None
                return mock_result
            if call_count["n"] == 2:
                # Engine chunks
                mock_result.scalars.return_value.all.return_value = engine_chunks
                return mock_result

        # Default: staged chunks (finalize_review / get_review_status)
        mock_result.scalars.return_value.all.return_value = staged_chunks
        return mock_result

    session.execute = AsyncMock(side_effect=fake_execute)
    return session


def _make_service(
    vector_store: AsyncMock | None = None,
    embedder: AsyncMock | None = None,
) -> PostIngestReviewService:
    """Build a PostIngestReviewService with mock providers."""
    vs = vector_store or AsyncMock()
    emb = embedder or AsyncMock()
    return PostIngestReviewService(vector_store=vs, embedder=emb)


# ---------------------------------------------------------------------------
# TestCreateReviewWorkspace
# ---------------------------------------------------------------------------


class TestCreateReviewWorkspace:
    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        """Creates workspace and copies engine chunks."""
        doc_v = FakeDocumentVersion()
        corpus_id = uuid.uuid4()
        chunks = [
            FakeEngineChunk(document_version_id=doc_v.id, chunk_index=0, text="Chunk 0"),
            FakeEngineChunk(document_version_id=doc_v.id, chunk_index=1, text="Chunk 1"),
        ]
        session = _make_session(doc_version=doc_v, engine_chunks=chunks)
        service = _make_service()

        result = await service.create_review_workspace(
            session, doc_v.id, corpus_id, user="reviewer"
        )

        assert result["document_version_id"] == doc_v.id
        assert result["corpus_id"] == corpus_id
        assert result["chunks_copied"] == 2
        assert result["status"] == "in_review"
        assert "workspace_id" in result
        # session.add called: 1 workspace + 2 chunks = 3
        assert session.add.call_count == 3

    @pytest.mark.asyncio
    async def test_workspace_records_its_kind_and_version(self) -> None:
        """M3/M4: the kind and the reviewed version are columns, not editable metadata."""
        doc_v = FakeDocumentVersion()
        chunks = [FakeEngineChunk(document_version_id=doc_v.id)]
        session = _make_session(doc_version=doc_v, engine_chunks=chunks)

        await _make_service().create_review_workspace(session, doc_v.id, uuid.uuid4(), user="u")

        workspace = session.add.call_args_list[0][0][0]
        assert workspace.kind == "post_ingest_review"
        assert workspace.reviewed_version_id == doc_v.id
        staged = session.add.call_args_list[1][0][0]
        assert staged.metadata_[ENGINE_CHUNK_ID_KEY] == str(chunks[0].id)

    @pytest.mark.asyncio
    async def test_inactive_version_is_refused(self) -> None:
        """H2: only the document's active version can be reviewed."""
        doc_v = FakeDocumentVersion(status="inactive")
        session = _make_session(doc_version=doc_v, engine_chunks=[FakeEngineChunk()])

        with pytest.raises(VersionNotActiveError):
            await _make_service().create_review_workspace(session, doc_v.id, uuid.uuid4(), user="u")
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_doc_not_found(self) -> None:
        """Raises DocumentNotFoundError when doc version doesn't exist."""
        session = _make_session(doc_version=None)
        service = _make_service()

        with pytest.raises(DocumentNotFoundError, match="not found"):
            await service.create_review_workspace(session, uuid.uuid4(), uuid.uuid4(), user="user")

    @pytest.mark.asyncio
    async def test_review_already_exists(self) -> None:
        """Raises ReviewAlreadyExistsError if workspace exists."""
        doc_v = FakeDocumentVersion()
        session = _make_session(doc_version=doc_v, existing_review=True)
        service = _make_service()

        with pytest.raises(ReviewAlreadyExistsError, match="already exists") as exc:
            await service.create_review_workspace(session, doc_v.id, uuid.uuid4(), user="user")
        assert exc.value.workspace_id == EXISTING_REVIEW_ID

    @pytest.mark.asyncio
    async def test_no_chunks(self) -> None:
        """Raises NoChunksToReviewError when doc has no chunks."""
        doc_v = FakeDocumentVersion()
        session = _make_session(doc_version=doc_v, engine_chunks=[])
        service = _make_service()

        with pytest.raises(NoChunksToReviewError, match="no chunks"):
            await service.create_review_workspace(session, doc_v.id, uuid.uuid4(), user="user")

    @pytest.mark.asyncio
    async def test_custom_title(self) -> None:
        """Custom title is used when provided."""
        doc_v = FakeDocumentVersion()
        chunks = [FakeEngineChunk(document_version_id=doc_v.id)]
        session = _make_session(doc_version=doc_v, engine_chunks=chunks)
        service = _make_service()

        result = await service.create_review_workspace(
            session,
            doc_v.id,
            uuid.uuid4(),
            user="user",
            title="My Custom Title",
        )

        assert result["title"] == "My Custom Title"

    @pytest.mark.asyncio
    async def test_default_title_contains_version_id(self) -> None:
        """Default title includes the document version ID."""
        doc_v = FakeDocumentVersion()
        chunks = [FakeEngineChunk(document_version_id=doc_v.id)]
        session = _make_session(doc_version=doc_v, engine_chunks=chunks)
        service = _make_service()

        result = await service.create_review_workspace(session, doc_v.id, uuid.uuid4(), user="user")

        assert str(doc_v.id) in result["title"]


# ---------------------------------------------------------------------------
# plan_new_version: the chunks a finalized review publishes (pure)
# ---------------------------------------------------------------------------

COUNTER = TokenCounter()


_ORDER = itertools.count()


def _engine(text: str, *, parent: object = None, table: bool = False, path=("Guide",)):
    return SimpleNamespace(
        id=uuid.uuid4(),
        chunk_index=next(_ORDER),
        text=text,
        parent_chunk_id=getattr(parent, "id", None),
        is_table=table,
        is_code=False,
        structure_path=list(path),
    )


def _copy(engine, index: int, **kw) -> FakeStagedChunk:
    return FakeStagedChunk(
        text=kw.pop("text", engine.text),
        chunk_index=index,
        metadata_={ENGINE_CHUNK_ID_KEY: str(engine.id)},
        **kw,
    )


def _family():
    p = _engine("a\n\nb\n\nc")
    kids = [_engine(t, parent=p) for t in ("a", "b", "c")]
    return p, kids


def _plan(original, staged, max_parent_tokens=2000):
    return plan_new_version(original, staged, counter=COUNTER, max_parent_tokens=max_parent_tokens)


class TestPlanNewVersion:
    def test_untouched_review_reproduces_the_structure(self) -> None:
        p, (a, b, c) = _family()
        staged = [_copy(x, i) for i, x in enumerate([p, a, b, c])]
        plan = _plan([p, a, b, c], staged)
        assert [x.text for x in plan.chunks] == [p.text, "a", "b", "c"]
        assert plan.chunks[0].is_parent and plan.chunks[0].unchanged
        assert [x.parent_index for x in plan.chunks] == [None, 0, 0, 0]
        assert (plan.unchanged, plan.edited, plan.added, plan.parents) == (3, 0, 0, 1)
        assert plan.original_dropped == 0

    def test_rejected_child_leaves_the_parent(self) -> None:
        p, (a, b, c) = _family()
        staged = [_copy(x, i) for i, x in enumerate([p, a, b, c])]
        staged[2].status = "rejected"
        plan = _plan([p, a, b, c], staged)
        assert plan.chunks[0].text == "a\n\nc"
        assert (plan.rejected, plan.original_dropped) == (1, 1)

    def test_rejected_parent_frees_its_children(self) -> None:
        p, (a, b, c) = _family()
        staged = [_copy(x, i) for i, x in enumerate([p, a, b, c])]
        staged[0].status = "rejected"
        staged[1].text = "A edited"
        plan = _plan([p, a, b, c], staged)
        assert [x.text for x in plan.chunks] == ["A edited", "b", "c"]
        assert all(x.parent_index is None and not x.is_parent for x in plan.chunks)
        assert (plan.edited, plan.parents) == (1, 0)

    def test_deleted_parent_frees_its_children(self) -> None:
        p, kids = _family()
        plan = _plan([p, *kids], [_copy(x, i) for i, x in enumerate(kids)])
        assert plan.parents == 0 and plan.original_dropped == 1

    def test_edit_to_a_parent_does_not_survive(self) -> None:
        p, (a, b, c) = _family()
        staged = [_copy(x, i) for i, x in enumerate([p, a, b, c])]
        staged[0].text = "hand-written parent"
        plan = _plan([p, a, b, c], staged)
        assert plan.chunks[0].text == p.text

    def test_one_surviving_child_drops_the_parent(self) -> None:
        p, (a, b, c) = _family()
        staged = [_copy(x, i) for i, x in enumerate([p, a, b, c])]
        staged[2].status = staged[3].status = "rejected"
        plan = _plan([p, a, b, c], staged)
        assert [x.text for x in plan.chunks] == ["a"]
        assert plan.parents == 0 and plan.original_dropped == 3

    def test_tables_count_as_children_but_not_as_parent_text(self) -> None:
        p = _engine("a\n\nb")
        a = _engine("a", parent=p)
        t = _engine("|x|", parent=p, table=True)
        b = _engine("b", parent=p)
        plan = _plan([p, a, t, b], [_copy(x, i) for i, x in enumerate([p, a, t, b])])
        assert plan.chunks[0].text == "a\n\nb"
        assert plan.chunks[2].is_table and plan.chunks[2].parent_index == 0

    def test_oversized_parent_is_dropped(self) -> None:
        p, (a, b, c) = _family()
        staged = [_copy(x, i) for i, x in enumerate([p, a, b, c])]
        plan = _plan([p, a, b, c], staged, max_parent_tokens=2)
        assert plan.parents == 0

    def test_added_and_split_chunks(self) -> None:
        p, (a, b, c) = _family()
        staged = [_copy(x, i) for i, x in enumerate([p, a, b, c])]
        half = _copy(a, 2, text="a2", source="manual")
        staged[1].text, staged[1].source = "a1", "manual"
        for later in staged[2:]:
            later.chunk_index += 1
        new = FakeStagedChunk(text="brand new", chunk_index=9, metadata_=None, source="manual")
        plan = _plan([p, a, b, c], [*staged, half, new])
        assert [x.text for x in plan.chunks] == [
            "a1\n\na2\n\nb\n\nc",
            "a1",
            "a2",
            "b",
            "c",
            "brand new",
        ]
        assert plan.chunks[-1].parent_index is None
        assert (plan.edited, plan.added, plan.original_dropped) == (2, 1, 0)

    def test_engine_ids_outside_the_version_are_new_chunks(self) -> None:
        """M4: an id typed into the metadata is only looked up inside the reviewed version."""
        a = _engine("a")
        forged = FakeStagedChunk(
            text="x", chunk_index=1, metadata_={ENGINE_CHUNK_ID_KEY: str(uuid.uuid4())}
        )
        junk = FakeStagedChunk(text="y", chunk_index=2, metadata_={ENGINE_CHUNK_ID_KEY: "nope"})
        plan = _plan([a], [_copy(a, 0), forged, junk])
        assert [x.origin for x in plan.chunks] == [a, None, None]
        assert plan.added == 2

    def test_new_chunk_takes_flags_and_path_from_its_metadata(self) -> None:
        new = FakeStagedChunk(
            text="|t|",
            metadata_={"is_table": True, "structure_path": ["Guide", "Prices"]},
        )
        (chunk,) = _plan([], [new]).chunks
        assert chunk.is_table and chunk.structure_path == ["Guide", "Prices"]

    def test_everything_rejected_is_empty(self) -> None:
        a = _engine("a")
        plan = _plan([a], [_copy(a, 0, status="rejected")])
        assert plan.chunks == [] and plan.original_dropped == 1


class TestAddedChunksJoinParents:
    """L-E: an added chunk joins a parent when the chunker would have grouped it there:
    same heading path as the parent's section, and next to that section's chunks."""

    def _added(self, text: str, index: int, path=("Guide",)) -> FakeStagedChunk:
        return FakeStagedChunk(
            text=text, chunk_index=index, source="manual", metadata_={"structure_path": list(path)}
        )

    def test_added_between_children_joins(self) -> None:
        p, (a, b, c) = _family()
        staged = [_copy(p, 0), _copy(a, 1), self._added("new", 2), _copy(b, 3), _copy(c, 4)]
        plan = _plan([p, a, b, c], staged)
        assert [x.text for x in plan.chunks] == ["a\n\nnew\n\nb\n\nc", "a", "new", "b", "c"]
        assert [x.parent_index for x in plan.chunks] == [None, 0, 0, 0, 0]
        assert plan.added == 1

    def test_added_after_the_last_child_joins(self) -> None:
        p, (a, b, c) = _family()
        staged = [*(_copy(x, i) for i, x in enumerate([p, a, b, c])), self._added("tail", 4)]
        plan = _plan([p, a, b, c], staged)
        assert plan.chunks[0].text == "a\n\nb\n\nc\n\ntail"
        assert plan.chunks[-1].parent_index == 0

    def test_added_before_the_first_child_joins(self) -> None:
        p, (a, b, c) = _family()
        staged = [
            _copy(p, 0),
            self._added("lead", 1),
            *(_copy(x, i + 2) for i, x in enumerate([a, b, c])),
        ]
        plan = _plan([p, a, b, c], staged)
        assert [x.text for x in plan.chunks] == ["lead\n\na\n\nb\n\nc", "lead", "a", "b", "c"]
        assert [x.parent_index for x in plan.chunks] == [None, 0, 0, 0, 0]

    def test_other_heading_path_stays_alone(self) -> None:
        p, (a, b, c) = _family()
        staged = [
            *(_copy(x, i) for i, x in enumerate([p, a, b])),
            self._added("elsewhere", 3, path=("Other",)),
            _copy(c, 4),
        ]
        plan = _plan([p, a, b, c], staged)
        assert plan.chunks[0].text == "a\n\nb\n\nc"
        loose = next(x for x in plan.chunks if x.text == "elsewhere")
        assert loose.parent_index is None

    def test_separated_from_the_section_stays_alone(self) -> None:
        p, (a, b, c) = _family()
        s = _engine("s", path=("Shipping",))
        staged = [*(_copy(x, i) for i, x in enumerate([p, a, b, c, s])), self._added("late", 5)]
        plan = _plan([p, a, b, c, s], staged)
        assert plan.chunks[-1].text == "late" and plan.chunks[-1].parent_index is None
        assert plan.chunks[0].text == "a\n\nb\n\nc"

    def test_added_chunk_keeps_a_parent_with_one_original_child_alive(self) -> None:
        p, (a, b, c) = _family()
        staged = [_copy(p, 0), _copy(a, 1), self._added("new", 2)]
        plan = _plan([p, a, b, c], staged)
        assert plan.chunks[0].is_parent and plan.chunks[0].text == "a\n\nnew"

    def test_no_join_when_the_parent_was_rejected(self) -> None:
        p, (a, b, c) = _family()
        staged = [_copy(p, 0, status="rejected"), _copy(a, 1), self._added("new", 2)]
        plan = _plan([p, a, b, c], staged)
        assert all(x.parent_index is None and not x.is_parent for x in plan.chunks)


class TestParentCopiesAreMarkers:
    """M-B: a staged chunk that descends from a parent (its copy, an edit of it, a split
    half or a merge that kept its metadata) only keeps the parent alive: its text is never
    published, and a text that differs from the parent's is reported as discarded."""

    def test_split_parent_publishes_the_children_once(self) -> None:
        p, (a, b, c) = _family()
        history = [{"action": "split"}]
        first = _copy(p, 0, text="a\n\nb", source="manual", edit_history=history)
        second = _copy(p, 1, text="c", source="manual", edit_history=history)
        staged = [first, second, *(_copy(x, i + 2) for i, x in enumerate([a, b, c]))]
        plan = _plan([p, a, b, c], staged)
        assert [x.text for x in plan.chunks] == [p.text, "a", "b", "c"]
        assert [(d.staged_chunk_id, d.parent_chunk_id, d.reason) for d in plan.discarded] == [
            (first.id, p.id, "split"),
            (second.id, p.id, "split"),
        ]

    def test_merge_that_kept_the_parents_metadata_is_a_marker(self) -> None:
        p, (a, b, c) = _family()
        merged = _copy(
            p, 0, text=f"{p.text}\n\nx", source="manual", edit_history=[{"action": "merge"}]
        )
        staged = [merged, *(_copy(x, i + 1) for i, x in enumerate([a, b, c]))]
        plan = _plan([p, a, b, c], staged)
        assert [x.text for x in plan.chunks if not x.is_parent] == ["a", "b", "c"]
        assert [d.reason for d in plan.discarded] == ["merge"]

    def test_edited_parent_text_is_reported(self) -> None:
        p, (a, b, c) = _family()
        staged = [_copy(x, i) for i, x in enumerate([p, a, b, c])]
        staged[0].text = "hand-written parent"
        staged[0].edit_history = [{"action": "edit_text"}]
        plan = _plan([p, a, b, c], staged)
        assert plan.chunks[0].text == p.text
        assert [(d.staged_chunk_id, d.reason) for d in plan.discarded] == [
            (staged[0].id, "edit_text")
        ]

    def test_untouched_parent_copy_is_not_reported(self) -> None:
        p, kids = _family()
        plan = _plan([p, *kids], [_copy(x, i) for i, x in enumerate([p, *kids])])
        assert plan.discarded == []

    def test_rejected_parent_edit_is_not_reported(self) -> None:
        p, kids = _family()
        staged = [_copy(x, i) for i, x in enumerate([p, *kids])]
        staged[0].text, staged[0].status = "edited then rejected", "rejected"
        assert _plan([p, *kids], staged).discarded == []


class TestFinalizeRefusals:
    """The checks made before any work; the work itself is tested against real Postgres
    (tests/integration/test_post_ingest_finalize.py)."""

    @staticmethod
    def _service_with(workspace: FakeWorkspace) -> PostIngestReviewService:
        service = _make_service()
        service._workspace = AsyncMock(return_value=workspace)  # type: ignore[method-assign]
        return service

    @pytest.mark.asyncio
    async def test_authoring_workspace(self) -> None:
        service = self._service_with(FakeWorkspace(kind="standard"))
        with pytest.raises(WrongWorkspaceKindError):
            await service.finalize_review(AsyncMock(), uuid.uuid4(), user="u", collection_name="c")

    @pytest.mark.asyncio
    async def test_already_finalized(self) -> None:
        service = self._service_with(FakeWorkspace(status="ingested"))
        with pytest.raises(WorkspaceNotInReviewError):
            await service.finalize_review(AsyncMock(), uuid.uuid4(), user="u", collection_name="c")

    @pytest.mark.asyncio
    async def test_reviewed_document_deleted(self) -> None:
        from callobuzz_rag.content_portal.services.post_ingest_review import (
            ReviewedDocumentDeletedError,
        )

        service = self._service_with(FakeWorkspace(reviewed_version_id=None))
        with pytest.raises(ReviewedDocumentDeletedError):
            await service.finalize_review(AsyncMock(), uuid.uuid4(), user="u", collection_name="c")

    def test_lock(self) -> None:
        from datetime import timedelta

        now = datetime.now(UTC)
        _check_lock(SimpleNamespace(locked_by=None, locked_at=None), "me", 30)
        _check_lock(SimpleNamespace(locked_by="me", locked_at=now), "me", 30)
        _check_lock(SimpleNamespace(locked_by="x", locked_at=now - timedelta(hours=1)), "me", 30)
        with pytest.raises(ReviewLockedError):
            _check_lock(SimpleNamespace(locked_by="x", locked_at=now), "me", 30)
        with pytest.raises(ReviewLockedError):  # 0 = locks never go stale
            _check_lock(SimpleNamespace(locked_by="x", locked_at=now - timedelta(days=9)), "me", 0)

    def test_error_statuses_and_codes(self) -> None:
        cases = {
            VersionNotActiveError: (409, "version_not_active"),
            WrongWorkspaceKindError: (409, "wrong_workspace_kind"),
            WorkspaceNotInReviewError: (409, "invalid_status_transition"),
            ReviewLockedError: (409, "workspace_locked"),
            NothingToFinalizeError: (422, "nothing_to_finalize"),
            ReviewAlreadyExistsError: (409, "review_exists"),
        }
        for cls, expected in cases.items():
            assert (cls.status_code, cls.code) == expected
        err = FinalizeFailedError("x", stage="embed")
        assert (err.status_code, err.code, err.stage) == (503, "finalize_failed", "embed")


# ---------------------------------------------------------------------------
# TestGetReviewStatus
# ---------------------------------------------------------------------------


class TestGetReviewStatus:
    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        """Returns correct chunk counts."""
        dv_id = str(uuid.uuid4())
        ws = FakeWorkspace(status="in_review", reviewed_version_id=uuid.UUID(dv_id))
        chunks = [
            FakeStagedChunk(
                workspace_id=ws.id,
                status="approved",
                metadata_={"document_version_id": dv_id},
            ),
            FakeStagedChunk(
                workspace_id=ws.id,
                status="rejected",
                metadata_={"document_version_id": dv_id},
            ),
            FakeStagedChunk(
                workspace_id=ws.id,
                status="draft",
                metadata_={"document_version_id": dv_id},
            ),
            FakeStagedChunk(
                workspace_id=ws.id,
                status="approved",
                metadata_={"document_version_id": dv_id},
                edit_history=[{"action": "edit_text"}],
            ),
        ]
        session = _make_session(workspace=ws, staged_chunks=chunks)
        service = _make_service()

        result = await service.get_review_status(session, ws.id)

        assert result["total_chunks"] == 4
        assert result["approved_chunks"] == 2
        assert result["rejected_chunks"] == 1
        assert result["draft_chunks"] == 1
        assert result["modified_chunks"] == 1
        assert result["document_version_id"] == uuid.UUID(dv_id)
        assert result["kind"] == "post_ingest_review"
        assert result["status"] == "in_review"

    @pytest.mark.asyncio
    async def test_empty_workspace(self) -> None:
        """Empty workspace returns all-zero counts."""
        ws = FakeWorkspace(status="in_review", reviewed_version_id=None)
        session = _make_session(workspace=ws, staged_chunks=[])
        service = _make_service()

        result = await service.get_review_status(session, ws.id)

        assert result["total_chunks"] == 0
        assert result["approved_chunks"] == 0
        assert result["rejected_chunks"] == 0
        assert result["draft_chunks"] == 0
        assert result["modified_chunks"] == 0
        assert result["document_version_id"] is None

    @pytest.mark.asyncio
    async def test_not_found(self) -> None:
        """Raises PostIngestReviewError for non-existent workspace."""
        session = _make_session(workspace=None)
        service = _make_service()

        with pytest.raises(PostIngestReviewError, match="not found"):
            await service.get_review_status(session, uuid.uuid4())


# ---------------------------------------------------------------------------
# TestWasModified
# ---------------------------------------------------------------------------


class TestWasModified:
    def test_unmodified_chunk(self) -> None:
        """Chunk with no edit_history is not modified."""
        chunk = FakeStagedChunk(edit_history=None)
        assert _was_modified(chunk) is False

    def test_empty_history(self) -> None:
        """Chunk with empty edit_history is not modified."""
        chunk = FakeStagedChunk(edit_history=[])
        assert _was_modified(chunk) is False

    def test_edit_text_action(self) -> None:
        """edit_text action counts as modified."""
        chunk = FakeStagedChunk(edit_history=[{"action": "edit_text", "user": "u1"}])
        assert _was_modified(chunk) is True

    def test_split_action(self) -> None:
        """split action counts as modified."""
        chunk = FakeStagedChunk(edit_history=[{"action": "split", "user": "u1"}])
        assert _was_modified(chunk) is True

    def test_merge_action(self) -> None:
        """merge action counts as modified."""
        chunk = FakeStagedChunk(edit_history=[{"action": "merge", "user": "u1"}])
        assert _was_modified(chunk) is True

    def test_rewrite_action(self) -> None:
        """rewrite action counts as modified."""
        chunk = FakeStagedChunk(edit_history=[{"action": "rewrite", "user": "u1"}])
        assert _was_modified(chunk) is True

    def test_non_modification_action(self) -> None:
        """Actions like 'status_change' do not count as modified."""
        chunk = FakeStagedChunk(edit_history=[{"action": "status_change", "user": "u1"}])
        assert _was_modified(chunk) is False

    def test_mixed_actions(self) -> None:
        """At least one modification action makes it modified."""
        chunk = FakeStagedChunk(
            edit_history=[
                {"action": "status_change"},
                {"action": "edit_text"},
            ]
        )
        assert _was_modified(chunk) is True


# ---------------------------------------------------------------------------
# TestSchemas
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_create_request_valid(self) -> None:
        from callobuzz_rag.content_portal.schemas.post_ingest import (
            CreatePostIngestReviewRequest,
        )

        req = CreatePostIngestReviewRequest(
            document_version_id=uuid.uuid4(),
            user="test_user",
        )
        assert req.title is None
        assert req.user == "test_user"

    def test_user_is_optional(self) -> None:
        """L5: `portal_actor` names the actor; the body's user is only a key's hint."""
        from callobuzz_rag.content_portal.schemas.post_ingest import (
            CreatePostIngestReviewRequest,
            FinalizeReviewRequest,
        )

        assert CreatePostIngestReviewRequest(document_version_id=uuid.uuid4()).user is None
        assert FinalizeReviewRequest().user is None

    def test_create_request_with_title(self) -> None:
        from callobuzz_rag.content_portal.schemas.post_ingest import (
            CreatePostIngestReviewRequest,
        )

        req = CreatePostIngestReviewRequest(
            document_version_id=uuid.uuid4(),
            user="test_user",
            title="My Review",
        )
        assert req.title == "My Review"

    def test_create_request_empty_user_rejected(self) -> None:
        from pydantic import ValidationError

        from callobuzz_rag.content_portal.schemas.post_ingest import (
            CreatePostIngestReviewRequest,
        )

        with pytest.raises(ValidationError):
            CreatePostIngestReviewRequest(
                document_version_id=uuid.uuid4(),
                user="",
            )

    def test_create_request_long_user_rejected(self) -> None:
        from pydantic import ValidationError

        from callobuzz_rag.content_portal.schemas.post_ingest import (
            CreatePostIngestReviewRequest,
        )

        with pytest.raises(ValidationError):
            CreatePostIngestReviewRequest(
                document_version_id=uuid.uuid4(),
                user="x" * 256,
            )

    def test_finalize_request_valid(self) -> None:
        from callobuzz_rag.content_portal.schemas.post_ingest import (
            FinalizeReviewRequest,
        )

        req = FinalizeReviewRequest(user="admin")
        assert req.user == "admin"

    def test_finalize_response_fields(self) -> None:
        from callobuzz_rag.content_portal.schemas.post_ingest import (
            FinalizeReviewResponse,
        )

        resp = FinalizeReviewResponse(
            workspace_id=uuid.uuid4(),
            status="ingested",
            message="Done",
            document_id=uuid.uuid4(),
            previous_version_id=uuid.uuid4(),
            new_version_id=uuid.uuid4(),
            chunks_in_new_version=6,
            chunks_unchanged=3,
            chunks_edited=1,
            chunks_added=1,
            parent_chunks=1,
            staged_chunks_rejected=1,
            previous_chunks_dropped=1,
            staged_chunks_total=6,
        )
        assert resp.chunks_in_new_version == 6

    def test_review_status_fields(self) -> None:
        from callobuzz_rag.content_portal.schemas.post_ingest import (
            PostIngestReviewStatus,
        )

        status = PostIngestReviewStatus(
            workspace_id=uuid.uuid4(),
            kind="post_ingest_review",
            document_version_id=None,
            corpus_id=uuid.uuid4(),
            status="in_review",
            total_chunks=10,
            approved_chunks=5,
            rejected_chunks=2,
            draft_chunks=3,
            modified_chunks=1,
        )
        assert status.document_version_id is None
        assert status.total_chunks == 10

    def test_create_response_fields(self) -> None:
        from callobuzz_rag.content_portal.schemas.post_ingest import (
            CreatePostIngestReviewResponse,
        )

        resp = CreatePostIngestReviewResponse(
            workspace_id=uuid.uuid4(),
            document_version_id=uuid.uuid4(),
            corpus_id=uuid.uuid4(),
            title="Test",
            status="in_review",
            chunks_copied=3,
            message="Created",
        )
        assert resp.chunks_copied == 3

    def test_title_max_length_rejected(self) -> None:
        from pydantic import ValidationError

        from callobuzz_rag.content_portal.schemas.post_ingest import (
            CreatePostIngestReviewRequest,
        )

        with pytest.raises(ValidationError):
            CreatePostIngestReviewRequest(
                document_version_id=uuid.uuid4(),
                user="test",
                title="x" * 501,
            )


# ---------------------------------------------------------------------------
# TestExceptionHierarchy
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    def test_post_ingest_review_error_inherits_portal_error(self) -> None:
        from callobuzz_rag.core.exceptions import ContentPortalError

        assert issubclass(PostIngestReviewError, ContentPortalError)

    def test_document_not_found_inherits(self) -> None:
        assert issubclass(DocumentNotFoundError, PostIngestReviewError)

    def test_review_already_exists_inherits(self) -> None:
        assert issubclass(ReviewAlreadyExistsError, PostIngestReviewError)

    def test_no_chunks_to_review_inherits(self) -> None:
        assert issubclass(NoChunksToReviewError, PostIngestReviewError)


class TestUnlinkedParents:
    """Ingest has never stored parent_chunk_id (the ids it linked were not assigned yet),
    so every existing document's parents look like ordinary chunks. The plan recognises
    them by the chunker's own rule: a chunk whose text is exactly its section's following
    chunks (tables and code left out) joined by blank lines."""

    @staticmethod
    def _unlinked():
        a, b = _engine("a"), _engine("b")
        t = _engine("|x|", table=True)
        p = _engine("a\n\nb")
        other = _engine("a\n\nb", path=("Other",))  # same text, next section: not a parent
        for index, chunk in enumerate((p, a, t, b, other)):
            chunk.parent_chunk_id = None
            chunk.chunk_index = index
        return p, a, t, b, other

    def test_parent_is_recognised(self) -> None:
        p, a, t, b, other = self._unlinked()
        staged = [_copy(x, i) for i, x in enumerate([p, a, t, b, other])]
        staged[1].status = "rejected"
        plan = _plan([p, a, t, b, other], staged)
        # The rejected child's text leaves the parent; with one text child left, plus the
        # table, the section still has two children.
        assert [x.text for x in plan.chunks] == ["b", "|x|", "b", "a\n\nb"]
        assert plan.chunks[0].is_parent
        assert [x.parent_index for x in plan.chunks] == [None, 0, 0, None]

    def test_stored_links_win(self) -> None:
        p, (a, b, c) = _family()
        plan = _plan([p, a, b, c], [_copy(x, i) for i, x in enumerate([p, a, b, c])])
        assert plan.parents == 1
