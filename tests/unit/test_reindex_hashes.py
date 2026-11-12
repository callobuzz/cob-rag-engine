"""`reindex --payload-hash` and `--rehash-blocks`: backfills that need no re-embedding."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from callobuzz_rag.pipeline.text_hash import text_hash
from callobuzz_rag.retrieval.reindex import backfill_text_hashes, paragraph_lines
from callobuzz_rag.vectordb.base import VectorStorageProvider


def _rows(*pairs: tuple[uuid.UUID, str, bool]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = [SimpleNamespace(id=i, text=t, active=a) for i, t, a in pairs]
    return result


def _session(*pages: MagicMock) -> AsyncMock:
    empty = MagicMock()
    empty.all.return_value = []
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[*pages, empty, *[MagicMock()] * 10])
    return session


CORPUS = SimpleNamespace(id=uuid.uuid4(), qdrant_collection="corpus_x")


async def test_payload_hash_dry_run_counts_and_writes_nothing() -> None:
    a, b = sorted([uuid.uuid4(), uuid.uuid4()])
    session = _session(_rows((a, "Returns are accepted.", True), (b, "Old version text.", False)))
    store = MagicMock(set_payloads=AsyncMock())

    done = await backfill_text_hashes(session, CORPUS, store, apply=False)

    assert (done.chunks, done.active) == (2, 1)
    store.set_payloads.assert_not_called()
    session.commit.assert_not_called()


async def test_payload_hash_apply_writes_postgres_and_active_points_only() -> None:
    a, b = sorted([uuid.uuid4(), uuid.uuid4()])
    session = _session(_rows((a, "Returns are accepted.", True), (b, "Old version text.", False)))
    store = MagicMock(set_payloads=AsyncMock())

    done = await backfill_text_hashes(session, CORPUS, store, apply=True)

    assert done.chunks == 2
    # One bulk UPDATE (second execute call: the first is the SELECT)
    update_call = session.execute.await_args_list[1]
    assert update_call.args[1] == [
        {"id": a, "text_hash": text_hash("Returns are accepted.")},
        {"id": b, "text_hash": text_hash("Old version text.")},
    ]
    # Qdrant: the active chunk's point only (an inactive version's points are gone)
    store.set_payloads.assert_awaited_once_with(
        "corpus_x", {str(a): {"text_hash": text_hash("Returns are accepted.")}}
    )
    session.commit.assert_awaited()


async def test_payload_hash_with_nothing_to_do_is_a_no_op() -> None:
    session = _session()
    store = MagicMock(set_payloads=AsyncMock())
    done = await backfill_text_hashes(session, CORPUS, store, apply=True)
    assert (done.chunks, done.active) == (0, 0)
    store.set_payloads.assert_not_called()


def test_paragraph_lines_drop_the_overlap_taken_from_the_previous_chunk() -> None:
    previous = "First paragraph here.\nThe warranty covers the motor for five years."
    text = "The warranty covers the motor for five years.\n\nSecond paragraph here."
    assert paragraph_lines(text, previous) == ["Second paragraph here."]
    assert paragraph_lines(text, None) == [
        "The warranty covers the motor for five years.",
        "Second paragraph here.",
    ]
    assert paragraph_lines("  \n\n ", None) == []


async def test_the_default_set_payloads_goes_through_update_payload() -> None:
    class Store(VectorStorageProvider):
        update_payload = AsyncMock()

        async def create_collection(self, name, vector_size): ...
        async def delete_collection(self, name): ...
        async def upsert(self, collection, points): ...
        async def search(self, collection, query_vector, limit=10, filters=None): ...
        async def search_sparse(self, collection, sparse_vector, limit=10, filters=None): ...
        async def delete(self, collection, ids): ...
        async def delete_by_filter(self, collection, filters): ...
        async def update_vectors(self, collection, points): ...
        async def health_check(self): ...

    store = Store()
    await store.set_payloads("c", {"p1": {"text_hash": "x"}, "p2": {"text_hash": "y"}})
    assert [c.args for c in Store.update_payload.await_args_list] == [
        ("c", {"chunk_id": "p1"}, {"text_hash": "x"}),
        ("c", {"chunk_id": "p2"}, {"text_hash": "y"}),
    ]


async def test_qdrant_sets_payloads_by_point_id_in_one_request() -> None:
    from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

    provider = QdrantVectorProvider.__new__(QdrantVectorProvider)
    provider._client = MagicMock(batch_update_points=AsyncMock())
    await provider.set_payloads("c", {"p1": {"text_hash": "x"}, "p2": {"text_hash": "y"}})
    call = provider._client.batch_update_points.await_args
    assert call.kwargs["collection_name"] == "c"
    ops = call.kwargs["update_operations"]
    assert [op.set_payload.points for op in ops] == [["p1"], ["p2"]]
    assert [op.set_payload.payload for op in ops] == [{"text_hash": "x"}, {"text_hash": "y"}]

    await provider.set_payloads("c", {})
    assert provider._client.batch_update_points.await_count == 1  # nothing to send
