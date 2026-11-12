"""Qdrant behaviours the smart-ingestion work relies on, against a real server.

- an empty upsert is a no-op (Qdrant answers an empty update with a 400,
  "Empty update request", which is how a document with no text used to fail its job);
- `text_hash` rides in the payload and comes back in search results;
- `set_payloads` (used by `reindex --payload-hash`) writes by point id in one request and
  leaves the rest of each payload alone.

Requires Docker.
"""

import uuid
from types import SimpleNamespace

import pytest

from callobuzz_rag.vectordb.base import ChunkPayload, VectorPoint
from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

pytestmark = pytest.mark.integration

DIM = 4


@pytest.fixture
async def store(qdrant_container):
    url = f"http://127.0.0.1:{qdrant_container.get_exposed_port(6333)}"
    provider = QdrantVectorProvider(
        SimpleNamespace(qdrant_url=url, qdrant_api_key_or_none=None, qdrant_collection="unused")
    )
    name = f"smart_{uuid.uuid4().hex[:8]}"
    await provider.create_collection(name, DIM)
    yield provider, name
    await provider.delete_collection(name)
    await provider.close()


def _point(text_hash: str | None) -> VectorPoint:
    chunk_id = str(uuid.uuid4())
    payload = ChunkPayload(
        chunk_id=chunk_id,
        corpus_id="c1",
        document_id=str(uuid.uuid4()),
        document_version_id=str(uuid.uuid4()),
        text_hash=text_hash,
    )
    return VectorPoint(id=chunk_id, vector=[0.1, 0.2, 0.3, 0.4], payload=payload.to_dict())


async def test_an_empty_upsert_is_a_no_op_on_a_real_server(store) -> None:
    provider, name = store
    await provider.upsert(name, [])  # raised UnexpectedResponse "Empty update request" before
    hits = await provider.search(name, [0.1, 0.2, 0.3, 0.4], 10, {"corpus_id": "c1"})
    assert hits == []


async def test_text_hash_round_trips_and_set_payloads_fills_the_gaps(store) -> None:
    provider, name = store
    hashed, legacy = _point("aaaaaaaaaaaaaaaa"), _point(None)
    await provider.upsert(name, [hashed, legacy])

    hits = {h.id: h.payload for h in await provider.search(name, [0.1, 0.2, 0.3, 0.4], 10)}
    assert hits[hashed.id]["text_hash"] == "aaaaaaaaaaaaaaaa"
    assert "text_hash" not in hits[legacy.id]

    await provider.set_payloads(name, {legacy.id: {"text_hash": "bbbbbbbbbbbbbbbb"}})
    hits = {h.id: h.payload for h in await provider.search(name, [0.1, 0.2, 0.3, 0.4], 10)}
    assert hits[legacy.id]["text_hash"] == "bbbbbbbbbbbbbbbb"
    assert hits[legacy.id]["document_id"] == legacy.payload["document_id"]  # nothing else lost
    assert hits[hashed.id]["text_hash"] == "aaaaaaaaaaaaaaaa"  # the other point untouched


async def test_the_default_set_payloads_path_also_works(store) -> None:
    """A store that does not override `set_payloads` falls back to `update_payload` by
    chunk_id; the Qdrant provider's filter path must agree with its by-id path."""
    from callobuzz_rag.vectordb.base import VectorStorageProvider

    provider, name = store
    point = _point(None)
    await provider.upsert(name, [point])
    await VectorStorageProvider.set_payloads(
        provider, name, {point.id: {"text_hash": "cccccccccccccccc"}}
    )
    (hit,) = await provider.search(name, [0.1, 0.2, 0.3, 0.4], 10)
    assert hit.payload["text_hash"] == "cccccccccccccccc"
