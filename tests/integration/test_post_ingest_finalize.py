"""Finalizing a post-ingest review against real PostgreSQL.

A review becomes a NEW version of the reviewed document, built from the workspace's
accepted staged chunks, with parents re-derived from their surviving children. The
reviewed version stays (inactive) and its points leave the collection. The vector store
and embedder are in-memory fakes; everything in Postgres is real, which is what the
parent/child foreign keys, row locks and rollbacks need. Requires Docker.
"""

import asyncio
import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from callobuzz_rag.chunking.chunker import StructureAwareChunker
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.secrets import hash_api_key
from callobuzz_rag.db.models.api_key import ApiKey
from callobuzz_rag.embeddings.base import EmbeddingResult
from callobuzz_rag.vectordb.base import VectorPoint
from tests.integration.conftest import new_tenant_id

pytestmark = pytest.mark.integration

ADMIN_KEY = "integration-finalize-admin-key"
REVIEWER = "reviewer@example.com"

PATH_RETURNS = ["Guide", "Returns"]
PATH_SHIPPING = ["Guide", "Shipping"]
C1 = "Items can be returned within 30 days of delivery."
C2 = "Refunds go back to the original payment method."
C3 = "Opened software cannot be returned for a refund."
S = "Orders ship within two business days."
T = "| Zone | Days |\n| EU | 3 |"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeStore:
    """Points per collection, with injectable failures."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, VectorPoint]] = {}
        self.main = ""  # the corpus's collection
        self.fail_upsert = False
        self.fail_delete_filter = False
        self.calls: list[tuple[str, str]] = []

    async def upsert(self, collection: str, points: list[VectorPoint]) -> None:
        self.calls.append(("upsert", collection))
        if self.fail_upsert:
            raise ConnectionError("qdrant down")
        bucket = self.collections.setdefault(collection, {})
        for p in points:
            bucket[p.id] = p

    async def delete_by_filter(self, collection: str, filters: dict[str, Any]) -> None:
        self.calls.append(("delete_by_filter", collection))
        if self.fail_delete_filter:
            raise ConnectionError("qdrant down")
        bucket = self.collections.get(collection, {})
        for pid in [
            pid
            for pid, p in bucket.items()
            if all(p.payload.get(k) == v for k, v in filters.items())
        ]:
            del bucket[pid]

    async def delete(self, collection: str, ids: list[str]) -> None:
        raise AssertionError("finalize must never delete points by id")

    def version_points(self, version_id: uuid.UUID, collection: str | None = None) -> list[Any]:
        return [
            p
            for p in self.collections.get(collection or self.main, {}).values()
            if p.payload.get("document_version_id") == str(version_id)
        ]


class FakeEmbedder:
    def __init__(self) -> None:
        self.fail = False
        self.calls = 0

    async def embed_texts(self, texts: list[str]) -> list[EmbeddingResult]:
        self.calls += 1
        if self.fail:
            raise ConnectionError("embedder down")
        return [
            EmbeddingResult(
                vector=[b / 255 for b in hashlib.sha256(t.encode()).digest()[:4]],
                model="fake-model",
                model_version="1",
                dimensions=4,
            )
            for t in texts
        ]

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# World: one corpus, one document whose active version has a parent + 3 children,
# a standalone chunk and a table; its points are in the fake store.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def world(db_engine) -> AsyncIterator[SimpleNamespace]:
    from callobuzz_rag.db.models.chunk import Chunk
    from callobuzz_rag.db.models.chunk_metadata import ChunkMetadata
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.document import Document
    from callobuzz_rag.db.models.document_version import DocumentVersion

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    w = SimpleNamespace(factory=factory, store=FakeStore(), embedder=FakeEmbedder())
    w.corpus_id, w.document_id, w.version_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    w.other_corpus_id = uuid.uuid4()
    w.ck = f"ck_{w.corpus_id.hex[:8]}_{uuid.uuid4().hex}"
    w.collection = w.store.main = f"corpus_{w.corpus_id.hex[:8]}"
    ids = {name: uuid.uuid4() for name in ("p", "c1", "c2", "c3", "s", "t", "foreign")}
    w.ids = ids
    async with factory() as session:
        tenant_id = await new_tenant_id(session)
        for cid in (w.corpus_id, w.other_corpus_id):
            session.add(
                Corpus(
                    id=cid,
                    name=f"finalize {cid.hex[:8]}",
                    tenant_id=tenant_id,
                    qdrant_collection=w.collection if cid == w.corpus_id else "other",
                )
            )
        await session.flush()
        for cid, key in ((w.corpus_id, w.ck), (w.other_corpus_id, "ck_other_key")):
            session.add(
                ApiKey(
                    corpus_id=cid,
                    id_short=str(cid)[:8],
                    name="Default key",
                    key_prefix="ck_...",
                    key_hash=hash_api_key(key),
                    permission="full",
                    is_default=True,
                )
            )
        await session.flush()
        session.add(
            Document(
                id=w.document_id,
                corpus_id=w.corpus_id,
                file_name="policy.md",
                doc_metadata={"category": "help", "team": "support"},
            )
        )
        other_doc = uuid.uuid4()
        session.add(Document(id=other_doc, corpus_id=w.other_corpus_id, file_name="x.md"))
        await session.flush()
        other_version = uuid.uuid4()
        session.add(DocumentVersion(id=w.version_id, document_id=w.document_id, status="active"))
        session.add(DocumentVersion(id=other_version, document_id=other_doc, status="active"))
        await session.flush()
        rows = [
            ("p", "\n\n".join([C1, C2, C3]), PATH_RETURNS, None, False),
            ("c1", C1, PATH_RETURNS, "p", False),
            ("c2", C2, PATH_RETURNS, "p", False),
            ("c3", C3, PATH_RETURNS, "p", False),
            ("s", S, PATH_SHIPPING, None, False),
            ("t", T, PATH_SHIPPING, None, True),
        ]
        for index, (name, text, path, parent, is_table) in enumerate(rows):
            session.add(
                Chunk(
                    id=ids[name],
                    document_version_id=w.version_id,
                    corpus_id=w.corpus_id,
                    chunk_index=index,
                    text=text,
                    structure_path=path,
                    page=1,
                    start_offset=index * 100,
                    end_offset=index * 100 + len(text),
                    length_tokens=len(text.split()),
                    is_table=is_table,
                    parent_chunk_id=ids[parent] if parent else None,
                )
            )
            await session.flush()
            session.add(
                ChunkMetadata(
                    chunk_id=ids[name],
                    keywords=[f"old-{name}"],
                    tags=["policy"],
                    table_json='{"rows": 1}' if is_table else None,
                )
            )
        session.add(
            Chunk(
                id=ids["foreign"],
                document_version_id=other_version,
                corpus_id=w.other_corpus_id,
                chunk_index=0,
                text="Another tenant's secret",
            )
        )
        await session.commit()
    for name in ("p", "c1", "c2", "c3", "s", "t"):
        await w.store.upsert(
            w.collection,
            [
                VectorPoint(
                    id=str(ids[name]),
                    vector=[0.0] * 4,
                    payload={
                        "document_version_id": str(w.version_id),
                        "document_id": str(w.document_id),
                    },
                )
            ],
        )
    await w.store.upsert(
        "other",
        [
            VectorPoint(
                id=str(ids["foreign"]),
                vector=[0.0] * 4,
                payload={"document_version_id": str(other_version)},
            )
        ],
    )
    w.store.calls.clear()
    yield w
    async with factory() as session:
        await session.execute(delete(Corpus).where(Corpus.id.in_([w.corpus_id, w.other_corpus_id])))
        await session.commit()


def _service(w: SimpleNamespace, settings: Settings | None = None):
    from callobuzz_rag.content_portal.services.post_ingest_review import PostIngestReviewService

    return PostIngestReviewService(
        vector_store=w.store,  # type: ignore[arg-type]
        embedder=w.embedder,  # type: ignore[arg-type]
        chunker=StructureAwareChunker(),
        settings=settings,
    )


async def _review(w: SimpleNamespace) -> uuid.UUID:
    async with w.factory() as session:
        result = await _service(w).create_review_workspace(
            session, w.version_id, w.corpus_id, user=REVIEWER
        )
        await session.commit()
    return result["workspace_id"]


async def _staged(w: SimpleNamespace, workspace_id: uuid.UUID) -> dict[str, Any]:
    """Staged chunks by the engine chunk name they were copied from."""
    from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk

    names = {str(v): k for k, v in w.ids.items()}
    async with w.factory() as session:
        rows = (
            await session.execute(
                select(StagedChunk).where(StagedChunk.workspace_id == workspace_id)
            )
        ).scalars()
        return {names[r.metadata_["engine_chunk_id"]]: r for r in rows}


async def _edit(w: SimpleNamespace, workspace_id: uuid.UUID, op: str, *args: Any, **kw: Any) -> Any:
    """Run one chunk-editor operation under the reviewer's lock, and commit."""
    from callobuzz_rag.content_portal.models.workspace import Workspace
    from callobuzz_rag.content_portal.services.chunk_editor import ChunkEditorService
    from callobuzz_rag.content_portal.services.workspace_service import WorkspaceService

    async with w.factory() as session:
        workspace = await session.get(Workspace, workspace_id)
        await WorkspaceService().acquire_lock(session, workspace, REVIEWER)
        result = await getattr(ChunkEditorService(), op)(
            session, workspace_id, *args, user=REVIEWER, **kw
        )
        await WorkspaceService().release_lock(session, workspace, REVIEWER)
        await session.commit()
    return result


async def _finalize(w: SimpleNamespace, workspace_id: uuid.UUID, **kw: Any) -> dict[str, Any]:
    async with w.factory() as session:
        return await _service(w, kw.pop("settings", None)).finalize_review(
            session, workspace_id, user=REVIEWER, collection_name=w.collection, **kw
        )


async def _version_chunks(w: SimpleNamespace, version_id: uuid.UUID) -> list[Any]:
    from sqlalchemy.orm import selectinload

    from callobuzz_rag.db.models.chunk import Chunk

    async with w.factory() as session:
        return list(
            (
                await session.execute(
                    select(Chunk)
                    .where(Chunk.document_version_id == version_id)
                    .options(selectinload(Chunk.metadata_record), selectinload(Chunk.embeddings))
                    .order_by(Chunk.chunk_index)
                )
            )
            .scalars()
            .all()
        )


async def _statuses(w: SimpleNamespace) -> dict[uuid.UUID, str]:
    from callobuzz_rag.db.models.document_version import DocumentVersion

    async with w.factory() as session:
        rows = await session.execute(
            select(DocumentVersion.id, DocumentVersion.status).where(
                DocumentVersion.document_id == w.document_id
            )
        )
        return {r.id: r.status for r in rows}


def _parent_and_children(chunks: list[Any]) -> tuple[Any | None, list[Any]]:
    parents = [c for c in chunks if any(k.parent_chunk_id == c.id for k in chunks)]
    assert len(parents) <= 1
    parent = parents[0] if parents else None
    return parent, [c for c in chunks if parent is not None and c.parent_chunk_id == parent.id]


# ---------------------------------------------------------------------------
# What the new version holds
# ---------------------------------------------------------------------------


async def test_untouched_review_republishes_the_same_chunks_as_a_new_version(world) -> None:
    ws = await _review(world)
    result = await _finalize(world, ws)

    new = result["new_version_id"]
    assert result["previous_version_id"] == world.version_id
    assert await _statuses(world) == {world.version_id: "inactive", new: "active"}
    chunks = await _version_chunks(world, new)
    assert [c.text for c in chunks] == ["\n\n".join([C1, C2, C3]), C1, C2, C3, S, T]
    parent, children = _parent_and_children(chunks)
    assert parent is not None and [c.text for c in children] == [C1, C2, C3]
    assert (result["chunks_in_new_version"], result["chunks_unchanged"]) == (6, 5)
    assert (result["parent_chunks"], result["previous_chunks_dropped"]) == (1, 0)
    # Unedited chunks keep offsets and table JSON; metadata tags carry over.
    table = chunks[5]
    assert table.is_table and table.metadata_record.table_json == '{"rows": 1}'
    assert chunks[1].start_offset == 100 and chunks[1].metadata_record.tags == ["policy"]
    # The old version is kept (restorable), its points are gone, the new ones are in.
    assert len(await _version_chunks(world, world.version_id)) == 6
    assert world.store.version_points(world.version_id) == []
    points = world.store.version_points(new)
    assert {p.id for p in points} == {str(c.id) for c in chunks}
    payload = points[0].payload
    assert payload["document_id"] == str(world.document_id)
    assert payload["corpus_id"] == str(world.corpus_id)
    assert payload["category"] == "help" and payload["meta_team"] == "support"
    assert all(p.sparse_vector is not None for p in points)
    assert all(len(c.embeddings) == 1 and c.embeddings[0].model == "fake-model" for c in chunks)


async def test_rejected_child_leaves_the_parent_text(world) -> None:
    """M1: a rejected child's text used to linger in its parent and stay searchable."""
    ws = await _review(world)
    staged = await _staged(world, ws)
    await _edit(world, ws, "reject_chunk", staged["c2"].id)
    result = await _finalize(world, ws)

    chunks = await _version_chunks(world, result["new_version_id"])
    assert all(C2 not in c.text for c in chunks)
    parent, children = _parent_and_children(chunks)
    assert parent.text == f"{C1}\n\n{C3}"
    assert [c.text for c in children] == [C1, C3]
    assert result["staged_chunks_rejected"] == 1
    assert result["previous_chunks_dropped"] == 1


async def test_rejected_parent_with_an_edited_child(world) -> None:
    """H1: this combination raised StaleDataError (the child row was deleted by the
    parent's cascade, then updated)."""
    ws = await _review(world)
    staged = await _staged(world, ws)
    await _edit(world, ws, "reject_chunk", staged["p"].id)
    await _edit(world, ws, "edit_chunk", staged["c1"].id, text="Returns: 45 days now.")
    result = await _finalize(world, ws)

    chunks = await _version_chunks(world, result["new_version_id"])
    assert [c.text for c in chunks] == ["Returns: 45 days now.", C2, C3, S, T]
    assert all(c.parent_chunk_id is None for c in chunks)
    assert (result["chunks_edited"], result["parent_chunks"]) == (1, 0)


async def test_edited_child_rebuilds_its_parent_and_its_enrichment(world) -> None:
    """L3: an edit gets a fresh token count, keywords and vectors, and the parent follows."""
    ws = await _review(world)
    staged = await _staged(world, ws)
    edited = "Items can be returned within 45 days. Warehouse inspection applies."
    await _edit(world, ws, "edit_chunk", staged["c1"].id, text=edited)
    result = await _finalize(world, ws)

    chunks = await _version_chunks(world, result["new_version_id"])
    parent, children = _parent_and_children(chunks)
    assert parent.text == "\n\n".join([edited, C2, C3])
    child = children[0]
    assert child.text == edited
    assert child.length_tokens == StructureAwareChunker().counter.count(edited)
    assert child.start_offset is None  # the old offsets no longer describe this text
    assert "old-c1" not in (child.metadata_record.keywords or [])
    assert child.metadata_record.keywords  # fresh extractive keywords
    point = next(
        p for p in world.store.version_points(result["new_version_id"]) if p.id == str(child.id)
    )
    assert (
        point.vector
        == (await FakeEmbedder().embed_texts([f"Guide > Returns\n\n{edited}"]))[0].vector
    )


async def test_added_chunk_is_published(world) -> None:
    ws = await _review(world)
    await _edit(world, ws, "add_chunk", text="Gift cards are not refundable.")
    result = await _finalize(world, ws)

    chunks = await _version_chunks(world, result["new_version_id"])
    assert chunks[-1].text == "Gift cards are not refundable."
    assert chunks[-1].parent_chunk_id is None
    assert result["chunks_added"] == 1


async def test_split_halves_stay_children_of_the_parent(world) -> None:
    ws = await _review(world)
    staged = await _staged(world, ws)
    await _edit(world, ws, "split_chunk", staged["c1"].id, position=C1.index(" within"))
    result = await _finalize(world, ws)

    chunks = await _version_chunks(world, result["new_version_id"])
    parent, children = _parent_and_children(chunks)
    assert [c.text for c in children] == [
        "Items can be returned",
        "within 30 days of delivery.",
        C2,
        C3,
    ]
    assert parent.text == "\n\n".join(c.text for c in children)
    assert result["chunks_edited"] == 2
    assert result["previous_chunks_dropped"] == 0


async def test_merged_chunk_stays_under_the_parent(world) -> None:
    ws = await _review(world)
    staged = await _staged(world, ws)
    await _edit(world, ws, "merge_chunks", chunk_ids=[staged["c2"].id, staged["c3"].id])
    result = await _finalize(world, ws)

    chunks = await _version_chunks(world, result["new_version_id"])
    parent, children = _parent_and_children(chunks)
    assert [c.text for c in children] == [C1, f"{C2}\n\n{C3}"]
    assert parent.text == f"{C1}\n\n{C2}\n\n{C3}"
    assert result["previous_chunks_dropped"] == 1  # c3 has no counterpart of its own


async def test_parent_with_one_surviving_child_is_dropped(world) -> None:
    ws = await _review(world)
    staged = await _staged(world, ws)
    await _edit(world, ws, "reject_chunk", staged["c2"].id)
    await _edit(world, ws, "delete_chunk", staged["c3"].id)
    result = await _finalize(world, ws)

    chunks = await _version_chunks(world, result["new_version_id"])
    assert [c.text for c in chunks] == [C1, S, T]
    assert all(c.parent_chunk_id is None for c in chunks)
    assert result["parent_chunks"] == 0
    assert result["previous_chunks_dropped"] == 3  # p, c2, c3


async def test_nothing_accepted_is_refused(world) -> None:
    from callobuzz_rag.content_portal.services.post_ingest_review import NothingToFinalizeError

    ws = await _review(world)
    for chunk in (await _staged(world, ws)).values():
        await _edit(world, ws, "reject_chunk", chunk.id)
    with pytest.raises(NothingToFinalizeError):
        await _finalize(world, ws)
    assert await _statuses(world) == {world.version_id: "active"}


# ---------------------------------------------------------------------------
# engine_chunk_id is editable metadata: it never reaches outside the reviewed version
# ---------------------------------------------------------------------------


async def test_engine_chunk_id_pointing_elsewhere_touches_nothing(world) -> None:
    """M4: pointing a staged chunk at another corpus's chunk neither reads nor writes it."""
    ws = await _review(world)
    staged = await _staged(world, ws)
    forged = {**staged["s"].metadata_, "engine_chunk_id": str(world.ids["foreign"])}
    await _edit(world, ws, "edit_chunk", staged["s"].id, metadata=forged, text="Changed")
    result = await _finalize(world, ws)

    from callobuzz_rag.db.models.chunk import Chunk

    async with world.factory() as session:
        foreign = await session.get(Chunk, world.ids["foreign"])
    assert foreign is not None and foreign.text == "Another tenant's secret"
    assert str(world.ids["foreign"]) in world.store.collections["other"]
    assert result["chunks_added"] == 1  # treated as a new chunk, not as the foreign one
    assert ("upsert", "other") not in world.store.calls


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


async def test_version_no_longer_active_is_refused(world) -> None:
    """H2: re-ingested / restored since the review began."""
    from callobuzz_rag.content_portal.services.post_ingest_review import VersionNotActiveError
    from callobuzz_rag.db.models.document_version import DocumentVersion

    ws = await _review(world)
    async with world.factory() as session:
        (await session.get(DocumentVersion, world.version_id)).status = "inactive"
        await session.commit()
    with pytest.raises(VersionNotActiveError):
        await _finalize(world, ws)
    with pytest.raises(VersionNotActiveError):  # and a review of it cannot be created
        async with world.factory() as session:
            await _service(world).create_review_workspace(
                session, world.version_id, world.corpus_id, user=REVIEWER
            )


async def test_lock_held_by_someone_else_is_refused(world) -> None:
    from datetime import UTC, datetime, timedelta

    from callobuzz_rag.content_portal.models.workspace import Workspace
    from callobuzz_rag.content_portal.services.post_ingest_review import ReviewLockedError

    ws = await _review(world)
    async with world.factory() as session:
        workspace = await session.get(Workspace, ws)
        workspace.locked_by, workspace.locked_at = "someone-else", datetime.now(UTC)
        await session.commit()
    with pytest.raises(ReviewLockedError):
        await _finalize(world, ws)

    async with world.factory() as session:  # a stale lock does not block
        workspace = await session.get(Workspace, ws)
        workspace.locked_at = datetime.now(UTC) - timedelta(hours=2)
        await session.commit()
    result = await _finalize(world, ws, lock_timeout_minutes=30)
    async with world.factory() as session:
        workspace = await session.get(Workspace, ws)
    assert workspace.locked_by is None and workspace.status == "ingested"
    assert workspace.finalized_version_id == result["new_version_id"]


async def test_concurrent_finalizes_publish_once(world) -> None:
    from callobuzz_rag.content_portal.services.post_ingest_review import (
        WorkspaceNotInReviewError,
    )

    ws = await _review(world)
    outcomes = await asyncio.gather(
        _finalize(world, ws), _finalize(world, ws), return_exceptions=True
    )
    published = [o for o in outcomes if isinstance(o, dict)]
    refused = [o for o in outcomes if isinstance(o, WorkspaceNotInReviewError)]
    assert (len(published), len(refused)) == (1, 1), outcomes
    statuses = await _statuses(world)
    assert sorted(statuses.values()) == ["active", "inactive"]


async def test_authoring_workspace_cannot_be_finalized(world) -> None:
    from callobuzz_rag.content_portal.services.post_ingest_review import WrongWorkspaceKindError
    from callobuzz_rag.content_portal.services.workspace_service import WorkspaceService

    async with world.factory() as session:
        workspace = await WorkspaceService().create_workspace(
            session, world.corpus_id, "Docs", "ana"
        )
        workspace.status = "in_review"
        await session.commit()
    with pytest.raises(WrongWorkspaceKindError):
        await _finalize(world, workspace.id)


# ---------------------------------------------------------------------------
# Failures: nothing half-done
# ---------------------------------------------------------------------------


async def test_embedding_failure_changes_nothing(world) -> None:
    from callobuzz_rag.content_portal.services.post_ingest_review import FinalizeFailedError

    ws = await _review(world)
    world.embedder.fail = True
    with pytest.raises(FinalizeFailedError) as err:
        await _finalize(world, ws)
    assert err.value.stage == "embed"
    assert await _statuses(world) == {world.version_id: "active"}
    assert world.store.calls == []


async def test_vector_store_failure_is_undone(world) -> None:
    from callobuzz_rag.content_portal.services.post_ingest_review import FinalizeFailedError

    ws = await _review(world)
    world.store.fail_upsert = True
    with pytest.raises(FinalizeFailedError) as err:
        await _finalize(world, ws)
    assert err.value.stage == "vector_store"
    assert await _statuses(world) == {world.version_id: "active"}
    assert len(world.store.version_points(world.version_id)) == 6
    assert len(world.store.collections[world.collection]) == 6


async def test_failure_after_old_points_left_puts_them_back(world) -> None:
    """The old points' delete fails after it may have applied: new points go, old come back."""
    from callobuzz_rag.content_portal.services.post_ingest_review import FinalizeFailedError

    ws = await _review(world)
    staged = await _staged(world, ws)
    await _edit(world, ws, "edit_chunk", staged["s"].id, text="Orders ship next day.")
    store = world.store
    real_delete = store.delete_by_filter

    async def delete_then_fail(collection: str, filters: dict[str, Any]) -> None:
        await real_delete(collection, filters)
        if filters["document_version_id"] == str(world.version_id):
            raise TimeoutError("response lost")

    store.delete_by_filter = delete_then_fail  # type: ignore[method-assign]
    with pytest.raises(FinalizeFailedError):
        await _finalize(world, ws)
    assert await _statuses(world) == {world.version_id: "active"}
    old = {p.id for p in store.version_points(world.version_id)}
    assert old == {str(world.ids[n]) for n in ("p", "c1", "c2", "c3", "s", "t")}
    assert len(store.collections[world.collection]) == 6  # no point of the unpublished version


async def test_dual_write_during_an_embedding_migration(world, monkeypatch) -> None:
    """L3: ingest dual-writes into a migration's new collection; so does finalize."""
    from callobuzz_rag.migration import context

    dual_embedder = FakeEmbedder()

    async def migration(session: Any, corpus_id: Any, settings: Any) -> Any:
        return context.DualWriteTarget(embedder=dual_embedder, collection="next", model="m2")

    monkeypatch.setattr(context, "get_migration_context", migration)
    await world.store.upsert(
        "next",
        [
            VectorPoint(
                id="old", vector=[0.0], payload={"document_version_id": str(world.version_id)}
            )
        ],
    )
    ws = await _review(world)
    result = await _finalize(world, ws, settings=Settings(_env_file=None))

    assert len(world.store.version_points(result["new_version_id"], "next")) == 6
    assert world.store.version_points(world.version_id, "next") == []
    chunks = await _version_chunks(world, result["new_version_id"])
    assert sorted(e.model for e in chunks[0].embeddings) == ["fake-model", "m2"]


# ---------------------------------------------------------------------------
# Through HTTP: coded 409s and the finalize response
# ---------------------------------------------------------------------------


def _app(db_engine, w: SimpleNamespace):
    from callobuzz_rag.api.app import create_app

    with patch.dict(os.environ, {"RAG_ENABLE_CONTENT_PORTAL": "true"}):
        app = create_app()
    app.state.settings = Settings(
        _env_file=None, admin_api_key=ADMIN_KEY, enable_content_portal=True
    )
    app.state.session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    app.state.vector_store = w.store
    app.state.embedder = w.embedder
    return app


async def _post(app, path: str, key: str = ADMIN_KEY, **kw: Any):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.post(path, headers={"X-API-Key": key}, **kw)


async def test_http_finalize_and_coded_refusals(db_engine, world) -> None:
    app = _app(db_engine, world)
    created = await _post(
        app,
        "/api/v1/portal/workspaces/from-ingestion",
        key=world.ck,
        json={"document_version_id": str(world.version_id)},  # `user` is optional now
    )
    assert created.status_code == 200, created.text
    ws = created.json()["workspace_id"]

    again = await _post(
        app,
        "/api/v1/portal/workspaces/from-ingestion",
        json={"document_version_id": str(world.version_id)},
    )
    assert (again.status_code, again.json()["code"]) == (409, "review_exists")
    assert again.json()["workspace_id"] == ws

    ingest = await _post(app, f"/api/v1/portal/workspaces/{ws}/ingest", json={"user": REVIEWER})
    assert (ingest.status_code, ingest.json()["code"]) == (409, "wrong_workspace_kind")
    approve = await _post(app, f"/api/v1/portal/workspaces/{ws}/approve", json={"user": REVIEWER})
    assert (approve.status_code, approve.json()["code"]) == (409, "wrong_workspace_kind")

    done = await _post(app, f"/api/v1/portal/workspaces/{ws}/finalize-review")
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["chunks_in_new_version"] == 6 and body["status"] == "ingested"

    twice = await _post(app, f"/api/v1/portal/workspaces/{ws}/finalize-review")
    assert (twice.status_code, twice.json()["code"]) == (409, "invalid_status_transition")

    stale = await _post(
        app,
        "/api/v1/portal/workspaces/from-ingestion",
        json={"document_version_id": str(world.version_id)},
    )
    assert (stale.status_code, stale.json()["code"]) == (409, "version_not_active")

    status = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        status = await client.get(
            f"/api/v1/portal/workspaces/{ws}/review-status", headers={"X-API-Key": ADMIN_KEY}
        )
    assert status.status_code == 200, status.text
    assert status.json()["kind"] == "post_ingest_review"
    assert status.json()["finalized_version_id"] == body["new_version_id"]


async def test_http_vector_store_failure_is_a_coded_503(db_engine, world) -> None:
    app = _app(db_engine, world)
    ws = await _review(world)
    world.store.fail_upsert = True
    res = await _post(app, f"/api/v1/portal/workspaces/{ws}/finalize-review", json={})
    assert res.status_code == 503, res.text
    assert res.json()["code"] == "finalize_failed"
    assert res.json()["stage"] == "vector_store"


async def test_http_lock_and_kind_refusals(db_engine, world) -> None:
    from datetime import UTC, datetime

    from callobuzz_rag.content_portal.models.workspace import Workspace

    app = _app(db_engine, world)
    ws = await _review(world)
    async with world.factory() as session:
        workspace = await session.get(Workspace, ws)
        workspace.locked_by, workspace.locked_at = "someone-else", datetime.now(UTC)
        await session.commit()
    locked = await _post(
        app, f"/api/v1/portal/workspaces/{ws}/finalize-review", json={"user": "me"}
    )
    assert (locked.status_code, locked.json()["code"]) == (409, "workspace_locked")
    # The lock holder may finalize.
    own = await _post(
        app, f"/api/v1/portal/workspaces/{ws}/finalize-review", json={"user": "someone-else"}
    )
    assert own.status_code == 200, own.text

    authoring = await _post(
        app, f"/api/v1/portal/workspaces?corpus_id={world.corpus_id}", json={"title": "t"}
    )
    wid = authoring.json()["id"]
    assert authoring.json()["kind"] == "standard"
    refused = await _post(app, f"/api/v1/portal/workspaces/{wid}/finalize-review")
    assert (refused.status_code, refused.json()["code"]) == (409, "wrong_workspace_kind")


async def test_document_ingested_without_parent_links(world) -> None:
    """Ingest never stored parent_chunk_id; the parent is recognised by its text."""
    from sqlalchemy import update

    from callobuzz_rag.db.models.chunk import Chunk

    async with world.factory() as session:
        await session.execute(
            update(Chunk)
            .where(Chunk.document_version_id == world.version_id)
            .values(parent_chunk_id=None)
        )
        await session.commit()
    ws = await _review(world)
    staged = await _staged(world, ws)
    await _edit(world, ws, "reject_chunk", staged["c2"].id)
    result = await _finalize(world, ws)

    chunks = await _version_chunks(world, result["new_version_id"])
    assert all(C2 not in c.text for c in chunks)
    parent, children = _parent_and_children(chunks)
    assert parent.text == f"{C1}\n\n{C3}"
    assert [c.text for c in children] == [C1, C3]


# ---------------------------------------------------------------------------
# Re-review follow-up (L-B, L-F, L-I)
# ---------------------------------------------------------------------------


async def test_restored_version_can_be_reviewed_again(world) -> None:
    """L-B: the finalized workspace of a version must not block reviewing it once restored."""
    from callobuzz_rag.db.models.document_version import DocumentVersion

    ws = await _review(world)
    result = await _finalize(world, ws)
    async with world.factory() as session:  # what POST .../versions/{v}/restore does
        (await session.get(DocumentVersion, result["new_version_id"])).status = "inactive"
        (await session.get(DocumentVersion, world.version_id)).status = "active"
        await session.commit()
    again = await _review(world)
    assert again != ws


async def test_points_carry_no_review_source_key(world) -> None:
    """L-F: nothing reads `review_source`; the payload is exactly ingest's."""
    ws = await _review(world)
    result = await _finalize(world, ws)
    points = world.store.version_points(result["new_version_id"])
    assert points and all("review_source" not in p.payload for p in points)


async def test_deleted_document_has_its_own_code(world) -> None:
    """L-I: a deleted document is not `version_not_active`."""
    from callobuzz_rag.content_portal.services.post_ingest_review import (
        ReviewedDocumentDeletedError,
    )
    from callobuzz_rag.db.models.document import Document

    ws = await _review(world)
    async with world.factory() as session:
        await session.execute(delete(Document).where(Document.id == world.document_id))
        await session.commit()
    with pytest.raises(ReviewedDocumentDeletedError) as err:
        await _finalize(world, ws)
    assert (err.value.status_code, err.value.code) == (409, "reviewed_document_deleted")


async def test_missing_workspace_is_a_404(world) -> None:
    """L-I: finalize of a workspace deleted meanwhile answered 400."""
    from callobuzz_rag.content_portal.services.post_ingest_review import PostIngestReviewError

    with pytest.raises(PostIngestReviewError) as err:
        await _finalize(world, uuid.uuid4())
    assert err.value.status_code == 404
    async with world.factory() as session:
        with pytest.raises(PostIngestReviewError) as status_err:
            await _service(world).get_review_status(session, uuid.uuid4())
    assert status_err.value.status_code == 404


# ---------------------------------------------------------------------------
# M-B: a parent's staged copy is a marker; its text cannot be changed
# ---------------------------------------------------------------------------


async def test_editor_refuses_to_change_a_parent_copys_text(world) -> None:
    from callobuzz_rag.content_portal.services.chunk_editor import ParentChunkDerivedError

    ws = await _review(world)
    staged = await _staged(world, ws)
    p, c1 = staged["p"], staged["c1"]
    refused = [
        ("split_chunk", (p.id,), {"position": 10}),
        ("merge_chunks", (), {"chunk_ids": [p.id, c1.id]}),
        ("merge_chunks", (), {"chunk_ids": [c1.id, p.id]}),
        ("edit_chunk", (p.id,), {"text": "hand-written parent"}),
        ("edit_chunk", (p.id,), {"metadata": {"engine_chunk_id": None}}),
        # pointing another chunk at the parent would make it a marker too
        (
            "edit_chunk",
            (c1.id,),
            {"metadata": {**c1.metadata_, "engine_chunk_id": str(p.metadata_["engine_chunk_id"])}},
        ),
    ]
    for op, args, kw in refused:
        with pytest.raises(ParentChunkDerivedError) as err:
            await _edit(world, ws, op, *args, **kw)
        assert err.value.code == "parent_chunk_derived", op
    # Status changes stay allowed: rejecting the parent frees its children.
    await _edit(world, ws, "reject_chunk", p.id)
    # A child's own metadata edit that keeps its link is fine.
    await _edit(world, ws, "edit_chunk", c1.id, metadata={**c1.metadata_, "note": "x"})
    after = await _staged(world, ws)
    assert after["p"].text == p.text and after["c1"].text == C1


async def test_ai_chat_refuses_to_change_a_parent_copys_text(world) -> None:
    from unittest.mock import MagicMock

    from callobuzz_rag.content_portal.services.ai_chat_service import (
        AIChatService,
        ParentChunkActionError,
    )

    ws = await _review(world)
    staged = await _staged(world, ws)
    p, c1 = staged["p"], staged["c1"]
    actions = [
        ("split_chunk", {"chunk_id": str(p.id), "split_points": [10]}),
        ("merge_chunks", {"chunk_ids": [str(c1.id), str(p.id)]}),
        ("rewrite_chunk", {"chunk_id": str(p.id), "new_text": "rewritten"}),
    ]
    for tool, arguments in actions:
        async with world.factory() as session:
            with pytest.raises(ParentChunkActionError):
                await AIChatService(llm=MagicMock()).execute_action(
                    session, ws, user=REVIEWER, tool_name=tool, arguments=arguments
                )
    # The child can still be rewritten.
    async with world.factory() as session:
        await AIChatService(llm=MagicMock()).execute_action(
            session,
            ws,
            user=REVIEWER,
            tool_name="rewrite_chunk",
            arguments={"chunk_id": str(c1.id), "new_text": "Returns: 45 days."},
        )


async def test_split_parent_from_an_older_review_publishes_children_once(world) -> None:
    """Reviews made before the editor refused it may hold split halves of a parent: both
    are markers, the children's text appears once, and the halves are reported."""
    from sqlalchemy import update

    from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk

    ws = await _review(world)
    staged = await _staged(world, ws)
    p = staged["p"]
    async with world.factory() as session:  # what the old split left behind
        await session.execute(
            update(StagedChunk)
            .where(StagedChunk.workspace_id == ws, StagedChunk.chunk_index >= 1)
            .values(chunk_index=StagedChunk.chunk_index + 1)
        )
        first = await session.get(StagedChunk, p.id)
        first.text, first.source = f"{C1}\n\n{C2}", "manual"
        first.edit_history = [{"action": "split"}]
        second = StagedChunk(
            id=uuid.uuid4(),
            workspace_id=ws,
            text=C3,
            chunk_index=1,
            status="draft",
            source="manual",
            metadata_=dict(p.metadata_),
            edit_history=[{"action": "split"}],
        )
        session.add(second)
        await session.commit()
    result = await _finalize(world, ws)

    chunks = await _version_chunks(world, result["new_version_id"])
    parent, children = _parent_and_children(chunks)
    assert [c.text for c in children] == [C1, C2, C3]
    assert parent.text == "\n\n".join([C1, C2, C3])
    assert sum(c.text == C1 for c in chunks) == 1
    assert sorted(
        (d["staged_chunk_id"], d["parent_chunk_id"], d["reason"])
        for d in result["discarded_parent_edits"]
    ) == sorted(
        [
            (p.id, world.ids["p"], "split"),
            (second.id, world.ids["p"], "split"),
        ]
    )
    assert "discarded" in result["message"]


async def test_http_split_of_a_parent_copy_is_a_coded_409(db_engine, world) -> None:
    app = _app(db_engine, world)
    ws = await _review(world)
    staged = await _staged(world, ws)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        headers = {"X-API-Key": ADMIN_KEY}
        lock = await client.post(
            f"/api/v1/portal/workspaces/{ws}/lock", headers=headers, json={"user": REVIEWER}
        )
        assert lock.status_code == 200, lock.text
        res = await client.post(
            f"/api/v1/portal/workspaces/{ws}/chunks/{staged['p'].id}/split",
            headers=headers,
            json={"user": REVIEWER, "position": 10},
        )
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "parent_chunk_derived"


# ---------------------------------------------------------------------------
# L-E: an added chunk joins the parent its heading path and position put it under
# ---------------------------------------------------------------------------


async def test_added_chunk_in_a_section_joins_its_parent(world) -> None:
    ws = await _review(world)
    added = await _edit(
        world,
        ws,
        "add_chunk",
        text="Store credit is issued for late returns.",
        metadata={"structure_path": PATH_RETURNS},
    )
    staged = await _staged_all(world, ws)
    order = [s.id for s in staged if s.id != added.id]
    order.insert(4, added.id)  # right after c3, the last child of the Returns section
    await _edit(world, ws, "reorder_chunks", chunk_ids=order)
    result = await _finalize(world, ws)

    chunks = await _version_chunks(world, result["new_version_id"])
    parent, children = _parent_and_children(chunks)
    assert [c.text for c in children] == [C1, C2, C3, "Store credit is issued for late returns."]
    assert parent.text.endswith("Store credit is issued for late returns.")
    assert result["chunks_added"] == 1


async def _staged_all(w: SimpleNamespace, workspace_id: uuid.UUID) -> list[Any]:
    from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk

    async with w.factory() as session:
        rows = await session.execute(
            select(StagedChunk)
            .where(StagedChunk.workspace_id == workspace_id)
            .order_by(StagedChunk.chunk_index)
        )
        return list(rows.scalars().all())


# ---------------------------------------------------------------------------
# M-C: build before locking; a held lock is a quick coded 409; re-checked under lock
# ---------------------------------------------------------------------------


async def _hold_row_lock(w: SimpleNamespace, sql: str, row_id: uuid.UUID) -> Any:
    """Open a transaction holding FOR UPDATE on one row (a long ingest, say)."""
    from sqlalchemy import text

    session = w.factory()
    await session.execute(text(sql), {"id": row_id})
    return session


@pytest.mark.parametrize(
    "sql, row",
    [
        ("SELECT 1 FROM documents WHERE id = :id FOR UPDATE", "document"),
        ("SELECT 1 FROM portal_workspaces WHERE id = :id FOR UPDATE", "workspace"),
    ],
)
async def test_a_held_lock_is_a_quick_coded_refusal(world, sql: str, row: str) -> None:
    import time

    from callobuzz_rag.content_portal.services.post_ingest_review import FinalizeBusyError

    ws = await _review(world)
    holder = await _hold_row_lock(world, sql, world.document_id if row == "document" else ws)
    try:
        started = time.monotonic()
        with pytest.raises(FinalizeBusyError) as err:
            await _finalize(world, ws, lock_wait_seconds=0.5)
        assert time.monotonic() - started < 5
    finally:
        await holder.rollback()
        await holder.close()
    assert (err.value.status_code, err.value.code) == (409, "document_busy")
    assert world.embedder.calls == 1  # the expensive build ran before any lock was taken
    assert world.store.calls == []  # nothing was written
    assert await _statuses(world) == {world.version_id: "active"}
    result = await _finalize(world, ws)  # and once the lock is gone, it goes through
    assert (await _statuses(world))[result["new_version_id"]] == "active"


async def test_staged_chunks_changed_during_the_build_are_refused(world) -> None:
    from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk
    from callobuzz_rag.content_portal.services.post_ingest_review import ReviewChangedError

    ws = await _review(world)
    staged = await _staged(world, ws)
    real = world.embedder.embed_texts

    async def edit_meanwhile(texts: list[str]) -> list[EmbeddingResult]:
        async with world.factory() as session:
            (await session.get(StagedChunk, staged["s"].id)).text = "Edited meanwhile."
            await session.commit()
        return await real(texts)

    world.embedder.embed_texts = edit_meanwhile  # type: ignore[method-assign]
    with pytest.raises(ReviewChangedError) as err:
        await _finalize(world, ws)
    assert (err.value.status_code, err.value.code) == (409, "review_changed")
    assert world.store.calls == []
    assert await _statuses(world) == {world.version_id: "active"}


async def test_version_replaced_during_the_build_is_refused(world) -> None:
    from callobuzz_rag.content_portal.services.post_ingest_review import VersionNotActiveError
    from callobuzz_rag.db.models.document_version import DocumentVersion

    ws = await _review(world)
    real = world.embedder.embed_texts

    async def replace_meanwhile(texts: list[str]) -> list[EmbeddingResult]:
        async with world.factory() as session:
            (await session.get(DocumentVersion, world.version_id)).status = "inactive"
            await session.commit()
        return await real(texts)

    world.embedder.embed_texts = replace_meanwhile  # type: ignore[method-assign]
    with pytest.raises(VersionNotActiveError):
        await _finalize(world, ws)
    assert world.store.calls == []


# ---------------------------------------------------------------------------
# L-D: database failures - rows, and a commit that fails (or only seems to)
# ---------------------------------------------------------------------------


async def test_row_write_failure_changes_nothing(world) -> None:
    from callobuzz_rag.content_portal.services.post_ingest_review import FinalizeFailedError

    ws = await _review(world)
    service = _service(world)
    real_build = service._build

    async def broken_build(*args: Any, **kw: Any) -> Any:
        built = await real_build(*args, **kw)
        built.rows[-1].corpus_id = uuid.uuid4()  # a foreign key the database refuses
        return built

    service._build = broken_build  # type: ignore[method-assign]
    async with world.factory() as session:
        with pytest.raises(FinalizeFailedError) as err:
            await service.finalize_review(
                session, ws, user=REVIEWER, collection_name=world.collection
            )
    assert err.value.stage == "database"
    assert world.store.calls == []
    assert await _statuses(world) == {world.version_id: "active"}
    from callobuzz_rag.content_portal.models.workspace import Workspace

    async with world.factory() as session:
        assert (await session.get(Workspace, ws)).status == "in_review"


async def test_commit_failure_is_undone(world) -> None:
    from callobuzz_rag.content_portal.services.post_ingest_review import FinalizeFailedError

    ws = await _review(world)
    async with world.factory() as session:

        async def failing_commit() -> None:
            raise ConnectionResetError("connection lost before COMMIT")

        session.commit = failing_commit  # type: ignore[method-assign]
        with pytest.raises(FinalizeFailedError) as err:
            await _service(world).finalize_review(
                session, ws, user=REVIEWER, collection_name=world.collection
            )
    assert err.value.stage == "database"
    assert await _statuses(world) == {world.version_id: "active"}
    old = {p.id for p in world.store.version_points(world.version_id)}
    assert old == {str(world.ids[n]) for n in ("p", "c1", "c2", "c3", "s", "t")}
    assert len(world.store.collections[world.collection]) == 6


async def test_commit_that_applied_despite_an_error_is_kept(world) -> None:
    """The COMMIT reached the database but its answer was lost: undoing the vector swap
    would leave the new active version with no points. It is re-checked and kept."""
    ws = await _review(world)
    async with world.factory() as session:
        real_commit = session.commit

        async def commit_then_fail() -> None:
            await real_commit()
            raise ConnectionResetError("connection lost after COMMIT")

        session.commit = commit_then_fail  # type: ignore[method-assign]
        result = await _service(world).finalize_review(
            session, ws, user=REVIEWER, collection_name=world.collection
        )
    new = result["new_version_id"]
    assert await _statuses(world) == {world.version_id: "inactive", new: "active"}
    assert len(world.store.version_points(new)) == 6
    assert world.store.version_points(world.version_id) == []
