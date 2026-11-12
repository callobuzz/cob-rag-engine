"""Unit tests for Qdrant vector storage provider with mocked client."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from qdrant_client import models
from qdrant_client.http.exceptions import UnexpectedResponse

from callobuzz_rag.vectordb.base import ChunkPayload, VectorPoint
from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.qdrant_url = "http://localhost:6333"
    settings.qdrant_collection = "test_chunks"
    return settings


@pytest.fixture
def provider(mock_settings):
    with patch("callobuzz_rag.vectordb.qdrant.AsyncQdrantClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        p = QdrantVectorProvider(mock_settings)
        p._client = mock_client
        return p


# --- Collection creation ---


async def test_create_collection(provider):
    """Creates collection with vector config."""
    await provider.create_collection("chunks", 1024)
    provider._client.create_collection.assert_awaited_once()
    call_kwargs = provider._client.create_collection.call_args[1]
    assert call_kwargs["collection_name"] == "chunks"
    assert "text" in call_kwargs["vectors_config"]
    assert "sparse_text" in call_kwargs["sparse_vectors_config"]
    # Keyword leg is TF-IDF: Qdrant applies IDF at query time.
    from qdrant_client import models

    sparse = call_kwargs["sparse_vectors_config"]["sparse_text"]
    assert sparse.modifier == models.Modifier.IDF


async def test_create_collection_idempotent(provider):
    """Collection creation is idempotent — ignores 'already exists'."""
    provider._client.create_collection.side_effect = UnexpectedResponse(
        status_code=409,
        reason_phrase="Conflict",
        content=b"Collection already exists",
        headers=httpx.Headers(),
    )
    # Should not raise
    await provider.create_collection("chunks", 1024)
    # ...and a pre-existing collection is upgraded to IDF in place.
    provider._client.update_collection.assert_awaited_once()
    assert (
        "sparse_text"
        in provider._client.update_collection.call_args.kwargs["sparse_vectors_config"]
    )


async def test_idf_upgrade_failure_is_not_fatal(provider):
    provider._client.create_collection.side_effect = UnexpectedResponse(
        status_code=409,
        reason_phrase="Conflict",
        content=b"Collection already exists",
        headers=httpx.Headers(),
    )
    provider._client.update_collection.side_effect = RuntimeError("old qdrant")
    await provider.create_collection("chunks", 1024)  # must not raise


async def test_create_collection_invalid_size(provider):
    """Rejects non-positive vector sizes."""
    with pytest.raises(ValueError, match="positive"):
        await provider.create_collection("chunks", 0)


# --- Upsert ---


async def test_upsert(provider):
    """Upserts points with named vectors."""
    points = [
        VectorPoint(id="id-1", vector=[0.1] * 1024, payload={"corpus_id": "c1"}),
        VectorPoint(id="id-2", vector=[0.2] * 1024, payload={"corpus_id": "c1"}),
    ]
    await provider.upsert("chunks", points)

    provider._client.upsert.assert_awaited_once()
    call_kwargs = provider._client.upsert.call_args[1]
    assert call_kwargs["collection_name"] == "chunks"
    assert len(call_kwargs["points"]) == 2
    # Verify named vector format
    assert call_kwargs["points"][0].vector == {"text": [0.1] * 1024}


# --- Search ---


async def test_search_basic(provider):
    """Search returns SearchResult list."""
    mock_point = MagicMock()
    mock_point.id = "id-1"
    mock_point.score = 0.95
    mock_point.payload = {"corpus_id": "c1", "chunk_id": "ch1"}

    mock_result = MagicMock()
    mock_result.points = [mock_point]
    provider._client.query_points = AsyncMock(return_value=mock_result)

    results = await provider.search("chunks", [0.1] * 1024, limit=5)

    assert len(results) == 1
    assert results[0].id == "id-1"
    assert results[0].score == 0.95
    assert results[0].payload["corpus_id"] == "c1"


async def test_search_with_filter(provider):
    """Search passes filters to Qdrant."""
    mock_result = MagicMock()
    mock_result.points = []
    provider._client.query_points = AsyncMock(return_value=mock_result)

    await provider.search("chunks", [0.1] * 1024, filters={"corpus_id": "c1"})

    call_kwargs = provider._client.query_points.call_args[1]
    assert call_kwargs["query_filter"] is not None
    assert call_kwargs["query_filter"].must is not None
    assert len(call_kwargs["query_filter"].must) == 1


async def test_search_empty_results(provider):
    """Search returns empty list when no matches."""
    mock_result = MagicMock()
    mock_result.points = []
    provider._client.query_points = AsyncMock(return_value=mock_result)

    results = await provider.search("chunks", [0.1] * 1024)
    assert results == []


# --- Delete ---


async def test_delete_by_ids(provider):
    """Delete by IDs uses PointIdsList."""
    await provider.delete("chunks", ["id-1", "id-2"])

    provider._client.delete.assert_awaited_once()
    call_kwargs = provider._client.delete.call_args[1]
    assert isinstance(call_kwargs["points_selector"], models.PointIdsList)


async def test_delete_by_filter(provider):
    """Delete by filter uses FilterSelector."""
    await provider.delete_by_filter("chunks", {"corpus_id": "c1"})

    provider._client.delete.assert_awaited_once()
    call_kwargs = provider._client.delete.call_args[1]
    assert isinstance(call_kwargs["points_selector"], models.FilterSelector)


# --- Filter building ---


def test_build_filter_exact_match(provider):
    """Single value builds MatchValue."""
    f = provider._build_filter({"corpus_id": "c1"})
    assert f.must is not None
    assert len(f.must) == 1
    assert f.must[0].key == "corpus_id"


def test_build_filter_match_any(provider):
    """List value builds MatchAny."""
    f = provider._build_filter({"keywords": ["AI", "ML"]})
    assert f.must is not None
    assert len(f.must) == 1


def test_build_filter_combined(provider):
    """Multiple keys produce multiple must conditions."""
    f = provider._build_filter({"corpus_id": "c1", "is_table": True})
    assert f.must is not None
    assert len(f.must) == 2


# --- Health check ---


async def test_health_check_success(provider):
    provider._client.get_collections = AsyncMock()
    assert await provider.health_check() is True


async def test_health_check_failure(provider):
    provider._client.get_collections = AsyncMock(side_effect=Exception("down"))
    assert await provider.health_check() is False


# --- ChunkPayload ---


def test_chunk_payload_to_dict():
    """ChunkPayload serializes to dict correctly."""
    payload = ChunkPayload(
        chunk_id="ch1",
        corpus_id="c1",
        document_id="d1",
        document_version_id="v1",
        page=5,
        is_table=True,
        language="en",
        keywords=["AI", "ML"],
    )
    d = payload.to_dict()
    assert d["chunk_id"] == "ch1"
    assert d["page"] == 5
    assert d["is_table"] is True
    assert d["keywords"] == ["AI", "ML"]


def test_chunk_payload_with_category_and_user_id():
    """ChunkPayload includes category and user_id in serialized dict."""
    payload = ChunkPayload(
        chunk_id="ch1",
        corpus_id="c1",
        document_id="d1",
        document_version_id="v1",
        category="product",
        user_id="user-123",
    )
    d = payload.to_dict()
    assert d["category"] == "product"
    assert d["user_id"] == "user-123"


def test_chunk_payload_metadata_flattened():
    """ChunkPayload flattens metadata dict with meta_ prefix."""
    payload = ChunkPayload(
        chunk_id="ch1",
        corpus_id="c1",
        document_id="d1",
        document_version_id="v1",
        metadata={"source": "shopify_sync", "variant_id": "12345"},
    )
    d = payload.to_dict()
    assert d["meta_source"] == "shopify_sync"
    assert d["meta_variant_id"] == "12345"
    assert "source" not in d


def test_chunk_payload_omits_none_category():
    """ChunkPayload does not include category/user_id if None."""
    payload = ChunkPayload(
        chunk_id="ch1",
        corpus_id="c1",
        document_id="d1",
        document_version_id="v1",
    )
    d = payload.to_dict()
    assert "category" not in d
    assert "user_id" not in d


def test_search_filters_with_metadata():
    """SearchFilters includes category, user_id, and metadata in Qdrant filters."""
    from callobuzz_rag.retrieval.models import SearchFilters

    sf = SearchFilters(
        corpus_id="c1",
        category="memory",
        user_id="user-456",
        metadata={"source": "conversation"},
    )
    f = sf.to_qdrant_filters()
    assert f["corpus_id"] == "c1"
    assert f["category"] == "memory"
    assert f["user_id"] == "user-456"
    assert f["meta_source"] == "conversation"


def test_search_filters_omits_none_fields():
    """SearchFilters does not include category/user_id if None."""
    from callobuzz_rag.retrieval.models import SearchFilters

    sf = SearchFilters(corpus_id="c1")
    f = sf.to_qdrant_filters()
    assert f == {"corpus_id": "c1"}
    assert "category" not in f
    assert "user_id" not in f
