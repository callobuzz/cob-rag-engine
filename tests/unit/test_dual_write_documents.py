"""Version restore, merge and delete honour a running embedding migration's dual-write.

They used to change only the corpus's own collection: the version a restore retired,
the one a merge retired and a deleted document's points all survived in the
migration's new collection (and came back at cutover), and a restored version was never
written there.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.api.routes.documents import delete_document, merge_documents, restore_version
from callobuzz_rag.embeddings.base import EmbeddingResult
from callobuzz_rag.migration.context import DualWriteTarget

pytestmark = pytest.mark.usefixtures("unlimited_plan")

MAIN = "corpus_x"
NEW = "corpus_x_v2"


def _embedder(dims: int) -> MagicMock:
    embedder = MagicMock()
    embedder.embed_texts = AsyncMock(
        side_effect=lambda texts: [
            EmbeddingResult(vector=[0.5] * dims, model="m", model_version="1", dimensions=dims)
            for _ in texts
        ]
    )
    embedder.close = AsyncMock()
    return embedder


def _store() -> MagicMock:
    store = MagicMock()
    store.upsert = AsyncMock()
    store.delete_by_filter = AsyncMock()
    store.update_payload = AsyncMock()
    return store


def _request(store: MagicMock) -> MagicMock:
    request = MagicMock()
    request.app.state.vector_store = store
    request.app.state.embedder = _embedder(2)
    request.app.state.settings = MagicMock()
    return request


def _result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    result.scalars.return_value.all.return_value = value
    return result


def _session(*results: object) -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[_result(r) for r in results])
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    return session


def _migrating(target: DualWriteTarget | None = None):  # type: ignore[no-untyped-def]
    return (
        patch(
            "callobuzz_rag.migration.context.get_dual_write_collection",
            AsyncMock(return_value=NEW),
        ),
        patch(
            "callobuzz_rag.migration.context.get_migration_context",
            AsyncMock(return_value=target),
        ),
    )


def _calls(mock: AsyncMock) -> dict[str, list[tuple[object, ...]]]:
    out: dict[str, list[tuple[object, ...]]] = {}
    for call in mock.await_args_list:
        out.setdefault(call.args[0], []).append(call.args[1:])
    return out


async def test_restore_during_migration_updates_both_collections() -> None:
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
        id=uuid.uuid4(), external_id=None, versions=[active, old], doc_metadata={"category": "faq"}
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
    store = _store()
    target = DualWriteTarget(embedder=_embedder(3), collection=NEW, model="new")
    p1, p2 = _migrating(target)
    with (
        p1,
        p2,
        patch(
            "callobuzz_rag.embeddings.factory.embedder_for_corpus", side_effect=lambda c, s, d: d
        ),
    ):
        await restore_version(_request(store), doc.id, old.id, _session(doc, [chunk], []), corpus)  # type: ignore[arg-type]

    deleted = _calls(store.delete_by_filter)
    assert deleted[MAIN] == [({"document_version_id": str(active.id)},)]
    assert deleted[NEW] == [({"document_version_id": str(active.id)},)]
    upserts = {c.args[0]: c.args[1] for c in store.upsert.await_args_list}
    assert set(upserts) == {MAIN, NEW}
    assert [p.id for p in upserts[NEW]] == [str(chunk.id)]
    assert upserts[NEW][0].payload == upserts[MAIN][0].payload
    assert len(upserts[NEW][0].vector) == 3  # the migration's model
    target.embedder.close.assert_awaited()  # type: ignore[attr-defined]
    assert (old.status, active.status) == ("active", "inactive")


async def test_merge_during_migration_updates_both_collections() -> None:
    corpus = SimpleNamespace(id=uuid.uuid4(), qdrant_collection=MAIN)
    retired = SimpleNamespace(id=uuid.uuid4(), status="active")
    moved = SimpleNamespace(id=uuid.uuid4(), status="active", document_id=None)
    old_doc = SimpleNamespace(id=uuid.uuid4(), external_id=None, versions=[retired])
    new_doc = SimpleNamespace(
        id=uuid.uuid4(),
        external_id=None,
        versions=[moved],
        content_fingerprint="f",
        size_bytes=1,
        mime_type="text/plain",
    )
    store = _store()
    p1, p2 = _migrating()
    with p1, p2:
        await merge_documents(
            _request(store), new_doc.id, old_doc.id, _session(None, old_doc, new_doc), corpus
        )  # type: ignore[arg-type]

    deleted = _calls(store.delete_by_filter)
    assert deleted[MAIN] == [({"document_version_id": str(retired.id)},)]
    assert deleted[NEW] == [({"document_version_id": str(retired.id)},)]
    updated = _calls(store.update_payload)
    for collection in (MAIN, NEW):
        assert updated[collection] == [
            ({"document_version_id": str(moved.id)}, {"document_id": str(old_doc.id)})
        ]


async def test_delete_during_migration_purges_both_collections() -> None:
    corpus = SimpleNamespace(id=uuid.uuid4(), qdrant_collection=MAIN)
    doc = SimpleNamespace(id=uuid.uuid4(), external_id=None, corpus_id=corpus.id, versions=[])
    store = _store()
    p1, p2 = _migrating()
    with p1, p2:
        await delete_document(_request(store), doc.id, _session(doc), corpus)  # type: ignore[arg-type]

    deleted = _calls(store.delete_by_filter)
    assert deleted == {
        MAIN: [({"document_id": str(doc.id)},)],
        NEW: [({"document_id": str(doc.id)},)],
    }
