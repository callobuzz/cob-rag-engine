"""Restoring a version is all-or-nothing (decided 2026-10-02).

It used to retire the active version and delete its vectors BEFORE it checked that the old
version has chunks (or embedded them): a 422 rolled the database back but the vectors were
gone, and the document stopped being searchable. Now everything is validated first, the
restored version's vectors are written BEFORE the current version's are removed, the database
is committed, and only then are the old vectors deleted. On any failure the current version
stays active and fully searchable.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from callobuzz_rag.api.routes.documents import restore_version
from callobuzz_rag.embeddings.base import EmbeddingResult

pytestmark = pytest.mark.usefixtures("unlimited_plan")

MAIN = "corpus_x"


def _embedder() -> MagicMock:
    embedder = MagicMock()
    embedder.embed_texts = AsyncMock(
        side_effect=lambda texts: [
            EmbeddingResult(vector=[0.5, 0.5], model="m", model_version="1", dimensions=2)
            for _ in texts
        ]
    )
    return embedder


def _world(*, chunks: bool = True):
    corpus = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        qdrant_collection=MAIN,
        embedding_config=None,
        applied_embedding=None,
    )
    active = SimpleNamespace(id=uuid.uuid4(), status="active")
    old = SimpleNamespace(id=uuid.uuid4(), status="inactive")
    doc = SimpleNamespace(
        id=uuid.uuid4(), external_id=None, versions=[active, old], doc_metadata=None
    )
    chunk = SimpleNamespace(
        id=uuid.uuid4(),
        document_version_id=old.id,
        text="Restored text.",
        structure_path=["Guide"],
        page=None,
        is_table=False,
        is_code=False,
        language="en",
        metadata_record=None,
    )

    def result(value: object) -> MagicMock:
        r = MagicMock()
        r.scalar_one_or_none.return_value = value
        r.scalars.return_value.all.return_value = value
        return r

    session = MagicMock()
    session.execute = AsyncMock(side_effect=[result(doc), result([chunk] if chunks else [])])
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()

    store = MagicMock()
    store.upsert = AsyncMock()
    store.delete_by_filter = AsyncMock()

    request = MagicMock()
    request.app.state.vector_store = store
    request.app.state.embedder = _embedder()
    request.app.state.settings = MagicMock()
    return SimpleNamespace(
        corpus=corpus,
        active=active,
        old=old,
        doc=doc,
        session=session,
        store=store,
        request=request,
    )


async def _restore(w, version_id=None):
    with (
        patch(
            "callobuzz_rag.embeddings.factory.embedder_for_corpus", side_effect=lambda c, s, d: d
        ),
        patch(
            "callobuzz_rag.migration.context.get_dual_write_collection",
            AsyncMock(return_value=None),
        ),
    ):
        return await restore_version(
            w.request, w.doc.id, version_id or w.old.id, w.session, w.corpus
        )


def _untouched(w) -> None:
    assert (w.active.status, w.old.status) == ("active", "inactive")
    w.store.delete_by_filter.assert_not_called()
    w.session.commit.assert_not_called()


async def test_a_version_without_chunks_is_a_422_and_the_current_one_is_untouched() -> None:
    w = _world(chunks=False)
    with pytest.raises(HTTPException) as err:
        await _restore(w)
    assert err.value.status_code == 422
    _untouched(w)
    w.store.upsert.assert_not_called()


async def test_an_unknown_version_is_a_404_and_nothing_changes() -> None:
    w = _world()
    with pytest.raises(HTTPException) as err:
        await _restore(w, uuid.uuid4())
    assert err.value.status_code == 404
    _untouched(w)


async def test_an_embedding_failure_leaves_the_current_version_searchable() -> None:
    w = _world()
    w.request.app.state.embedder.embed_texts = AsyncMock(side_effect=RuntimeError("tei down"))
    with pytest.raises(RuntimeError):
        await _restore(w)
    _untouched(w)
    w.store.upsert.assert_not_called()


async def test_an_upsert_failure_removes_only_the_restored_points_and_keeps_the_current() -> None:
    w = _world()
    w.store.upsert.side_effect = ConnectionError("qdrant down")
    with pytest.raises(ConnectionError):
        await _restore(w)
    assert (w.active.status, w.old.status) == ("active", "inactive")
    w.session.commit.assert_not_called()
    # whatever part of the restored version landed is cleaned up; the current one is not touched
    filters = [c.args[1] for c in w.store.delete_by_filter.await_args_list]
    assert filters == [{"document_version_id": str(w.old.id)}]


async def test_a_failed_commit_removes_the_restored_points_and_keeps_the_current() -> None:
    w = _world()
    w.session.commit.side_effect = RuntimeError("db gone")
    with pytest.raises(RuntimeError):
        await _restore(w)
    filters = [c.args[1] for c in w.store.delete_by_filter.await_args_list]
    assert filters == [{"document_version_id": str(w.old.id)}]
    w.session.rollback.assert_awaited()


async def test_the_old_vectors_go_only_after_the_new_ones_are_in_and_the_commit_is_done() -> None:
    w = _world()
    order = MagicMock()
    order.attach_mock(w.store.upsert, "upsert")
    order.attach_mock(w.session.commit, "commit")
    order.attach_mock(w.store.delete_by_filter, "delete")
    response = await _restore(w)
    assert [c[0] for c in order.mock_calls] == ["upsert", "commit", "delete"]
    assert w.store.delete_by_filter.await_args.args[1] == {"document_version_id": str(w.active.id)}
    assert (w.old.status, w.active.status) == ("active", "inactive")
    assert response.previous_active_version_id == w.active.id


async def test_a_failure_deleting_the_old_vectors_after_the_commit_does_not_undo_the_restore() -> (
    None
):
    w = _world()
    w.store.delete_by_filter.side_effect = ConnectionError("qdrant down")
    response = await _restore(w)
    assert response.restored_version_id == w.old.id
    assert w.old.status == "active"
    w.session.commit.assert_awaited_once()
