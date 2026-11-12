"""Heading context in indexed text, in-place vector updates, and the reindex pass."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from callobuzz_rag.chunking.index_text import index_text
from callobuzz_rag.embeddings.base import EmbeddingResult
from callobuzz_rag.retrieval.sparse import text_to_sparse_vector
from callobuzz_rag.vectordb.base import VectorPoint


class TestIndexText:
    def test_prefixes_heading_path(self) -> None:
        text = "It must be unused and in its original packaging."
        assert index_text(text, ["Help Center", "Returns"], heading_context=True) == (
            "Help Center > Returns\n\n" + text
        )

    def test_no_path_or_disabled_leaves_text_alone(self) -> None:
        assert index_text("body", None, heading_context=True) == "body"
        assert index_text("body", ["", "  "], heading_context=True) == "body"
        assert index_text("body", ["Returns"], heading_context=False) == "body"

    def test_record_title_reaches_the_keyword_vector(self) -> None:
        """A record's title is its heading: a query for the title term must match."""
        from callobuzz_rag.chunking.chunker import StructureAwareChunker
        from callobuzz_rag.pipeline.records import TextRecord

        record = TextRecord(id="t1", title="Tent footprint", text="Protects the floor.")
        chunk = StructureAwareChunker().chunk(record.to_parsed())[0]
        assert "footprint" not in chunk.text.lower()  # stored text stays the body
        vec = text_to_sparse_vector(index_text(chunk.text, chunk.structure_path))
        body_only = text_to_sparse_vector(chunk.text)
        assert len(vec.indices) > len(body_only.indices)


class TestQdrantUpdateVectors:
    async def test_writes_dense_and_sparse_and_keeps_payload(self) -> None:
        from callobuzz_rag.vectordb.base import SparseVector
        from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

        provider = QdrantVectorProvider.__new__(QdrantVectorProvider)
        provider._client = MagicMock(update_vectors=AsyncMock())
        await provider.update_vectors(
            "c",
            [
                VectorPoint(
                    id="p1",
                    vector=[0.1, 0.2],
                    payload={"ignored": True},
                    sparse_vector=SparseVector(indices=[3], values=[1.5]),
                ),
                VectorPoint(id="p2", vector=[], payload={}, sparse_vector=None),
            ],
        )
        points = provider._client.update_vectors.await_args.kwargs["points"]
        assert points[0].vector["text"] == [0.1, 0.2]
        assert points[0].vector["sparse_text"].indices == [3]
        # Empty dense = keep it; None sparse = clear it.
        assert "text" not in points[1].vector
        assert points[1].vector["sparse_text"].indices == []

    async def test_no_points_no_call(self) -> None:
        from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

        provider = QdrantVectorProvider.__new__(QdrantVectorProvider)
        provider._client = MagicMock(update_vectors=AsyncMock())
        await provider.update_vectors("c", [])
        provider._client.update_vectors.assert_not_called()


class TestReindexCorpus:
    def _chunks(self, n: int) -> list[SimpleNamespace]:
        ids = sorted(uuid.uuid4() for _ in range(n))
        return [
            SimpleNamespace(id=i, text=f"refund policy {k}", structure_path=["Returns"])
            for k, i in enumerate(ids)
        ]

    def _session(self, pages: list[list[SimpleNamespace]]) -> MagicMock:
        results = []
        for page in [*pages, []]:
            result = MagicMock()
            result.scalars.return_value.all.return_value = page
            results.append(result)
        return MagicMock(execute=AsyncMock(side_effect=results))

    async def test_keyword_only_pages_through_every_chunk(self) -> None:
        from callobuzz_rag.retrieval.reindex import reindex_corpus

        chunks = self._chunks(3)
        store = MagicMock(update_vectors=AsyncMock())
        corpus = SimpleNamespace(id=uuid.uuid4(), qdrant_collection="corpus_x")
        done = await reindex_corpus(
            self._session([chunks[:2], chunks[2:]]), corpus, store, batch_size=2
        )
        assert done == 3
        written = [p for call in store.update_vectors.await_args_list for p in call.args[1]]
        assert [p.id for p in written] == [str(c.id) for c in chunks]
        assert all(p.vector == [] for p in written)  # dense untouched
        expected = text_to_sparse_vector(index_text("refund policy 0", ["Returns"]))
        assert written[0].sparse_vector.indices == expected.indices

    async def test_dense_reembeds_the_index_text(self) -> None:
        from callobuzz_rag.retrieval.reindex import reindex_corpus

        chunks = self._chunks(1)
        embedder = MagicMock()
        embedder.embed_texts = AsyncMock(
            return_value=[EmbeddingResult(vector=[0.5], model="m", model_version="1", dimensions=1)]
        )
        store = MagicMock(update_vectors=AsyncMock())
        corpus = SimpleNamespace(id=uuid.uuid4(), qdrant_collection="corpus_x")
        await reindex_corpus(self._session([chunks]), corpus, store, embedder=embedder)
        assert embedder.embed_texts.await_args.args[0] == [
            index_text("refund policy 0", ["Returns"])
        ]
        assert store.update_vectors.await_args.args[1][0].vector == [0.5]


class TestQdrantSearchRetry:
    async def test_transport_error_is_retried_then_succeeds(self) -> None:
        from qdrant_client.http.exceptions import ResponseHandlingException

        from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

        provider = QdrantVectorProvider.__new__(QdrantVectorProvider)
        ok = SimpleNamespace(points=[SimpleNamespace(id="p1", score=0.9, payload={})])
        provider._client = MagicMock(
            query_points=AsyncMock(
                side_effect=[ResponseHandlingException(OSError("broken pipe")), ok]
            )
        )
        results = await provider.search("c", [0.1], limit=1)
        assert [r.id for r in results] == ["p1"]
        assert provider._client.query_points.await_count == 2

    async def test_http_errors_are_not_retried(self) -> None:
        import pytest
        from qdrant_client.http.exceptions import UnexpectedResponse

        from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

        provider = QdrantVectorProvider.__new__(QdrantVectorProvider)
        error = UnexpectedResponse(404, "Not Found", b"missing", MagicMock())
        provider._client = MagicMock(query_points=AsyncMock(side_effect=error))
        with pytest.raises(UnexpectedResponse):
            await provider.search("c", [0.1], limit=1)
        assert provider._client.query_points.await_count == 1


class TestChunkPayload:
    def test_rebuilt_payload_carries_metadata_and_keywords(self) -> None:
        """The embedding migration wrote keywords=[] and no metadata: filters broke."""
        from callobuzz_rag.pipeline.payload import chunk_payload

        chunk = SimpleNamespace(
            id=uuid.uuid4(),
            document_version_id=uuid.uuid4(),
            page=3,
            is_table=False,
            is_code=False,
            language="en",
        )
        payload = chunk_payload(
            chunk,
            "c1",
            "d1",
            {"category": "faq", "user_id": "u9", "region": "eu"},
            ["returns", "refund"],
        ).to_dict()
        assert payload["category"] == "faq"
        assert payload["user_id"] == "u9"
        assert payload["meta_region"] == "eu"
        assert payload["keywords"] == ["returns", "refund"]
        assert payload["document_version_id"] == str(chunk.document_version_id)


class TestRebuildCorpus:
    async def test_recreates_the_collection_with_full_payloads(self) -> None:
        from callobuzz_rag.retrieval.reindex import rebuild_corpus

        doc = SimpleNamespace(id=uuid.uuid4(), doc_metadata={"category": "policy"})
        chunk = SimpleNamespace(
            id=uuid.uuid4(),
            text="Unused items can be returned within 30 days.",
            structure_path=["Returns"],
            document_version_id=uuid.uuid4(),
            document_version=SimpleNamespace(document=doc),
            metadata_record=SimpleNamespace(keywords=["returns"]),
            page=None,
            is_table=False,
            is_code=False,
            language="en",
        )
        results = []
        for page in ([chunk], []):
            result = MagicMock()
            result.scalars.return_value.all.return_value = page
            results.append(result)
        session = MagicMock(execute=AsyncMock(side_effect=results))
        store = MagicMock(
            delete_collection=AsyncMock(), create_collection=AsyncMock(), upsert=AsyncMock()
        )
        embedder = MagicMock()
        embedder.embed_texts = AsyncMock(
            return_value=[EmbeddingResult(vector=[0.5], model="m", model_version="1", dimensions=1)]
        )
        corpus = SimpleNamespace(id=uuid.uuid4(), qdrant_collection="corpus_x")

        done = await rebuild_corpus(session, corpus, store, embedder, 768)

        assert done == 1
        store.delete_collection.assert_awaited_once_with("corpus_x")
        store.create_collection.assert_awaited_once_with("corpus_x", 768)
        point = store.upsert.await_args.args[1][0]
        assert point.payload["category"] == "policy"
        assert point.payload["keywords"] == ["returns"]
        assert point.payload["document_id"] == str(doc.id)
        assert point.sparse_vector is not None
