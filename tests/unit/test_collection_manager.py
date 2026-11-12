"""Tests for Qdrant collection lifecycle management."""

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.vectordb.base import VectorStorageProvider


class TestVectorStorageProviderABC:
    def test_delete_collection_is_abstract(self) -> None:
        """VectorStorageProvider ABC must declare delete_collection."""
        assert hasattr(VectorStorageProvider, "delete_collection")
        assert "delete_collection" in VectorStorageProvider.__abstractmethods__


class TestQdrantDeleteCollection:
    def _make_provider(self) -> "QdrantVectorProvider":
        from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

        settings = MagicMock()
        settings.qdrant_url = "http://localhost:6333"
        settings.qdrant_collection = "chunks"

        with patch("callobuzz_rag.vectordb.qdrant.AsyncQdrantClient") as mock_client_cls:
            mock_client_cls.return_value = AsyncMock()
            provider = QdrantVectorProvider(settings)
        return provider

    @pytest.mark.asyncio
    async def test_delete_collection_calls_client(self) -> None:
        """delete_collection should call the Qdrant client's delete_collection."""
        provider = self._make_provider()
        provider._client.delete_collection = AsyncMock()

        await provider.delete_collection("test_corpus_abc123")

        provider._client.delete_collection.assert_called_once_with(
            collection_name="test_corpus_abc123"
        )

    @pytest.mark.asyncio
    async def test_delete_collection_not_found_is_noop(self) -> None:
        """Deleting a non-existent collection should not raise."""
        from qdrant_client.http.exceptions import UnexpectedResponse

        provider = self._make_provider()
        provider._client.delete_collection = AsyncMock(
            side_effect=UnexpectedResponse(
                status_code=404,
                reason_phrase="Not Found",
                content=b"Collection not found",
                headers={},
            )
        )

        await provider.delete_collection("nonexistent_collection")

    @pytest.mark.asyncio
    async def test_delete_collection_unexpected_error_raises(self) -> None:
        """Unexpected Qdrant errors should propagate."""
        from qdrant_client.http.exceptions import UnexpectedResponse

        provider = self._make_provider()
        provider._client.delete_collection = AsyncMock(
            side_effect=UnexpectedResponse(
                status_code=500,
                reason_phrase="Internal Server Error",
                content=b"something broke",
                headers={},
            )
        )

        with pytest.raises(UnexpectedResponse):
            await provider.delete_collection("bad_collection")


class TestCollectionManager:
    def _make_mock_vector_store(self) -> AsyncMock:
        """Create a mock VectorStorageProvider with all needed methods."""
        store = AsyncMock(spec=VectorStorageProvider)
        store.create_collection = AsyncMock()
        store.delete_collection = AsyncMock()
        store.health_check = AsyncMock(return_value=True)
        return store

    def _make_manager(
        self,
        vector_store: AsyncMock | None = None,
        embedding_dimensions: int = 1024,
    ) -> "CollectionManager":
        from callobuzz_rag.vectordb.collection_manager import CollectionManager

        store = vector_store or self._make_mock_vector_store()
        return CollectionManager(
            vector_store=store,
            embedding_dimensions=embedding_dimensions,
        )

    @pytest.mark.asyncio
    async def test_create_for_corpus_calls_create_collection(self) -> None:
        store = self._make_mock_vector_store()
        manager = self._make_manager(vector_store=store)
        await manager.create_for_corpus("corpus_abc123")
        store.create_collection.assert_called_once_with("corpus_abc123", 1024)

    @pytest.mark.asyncio
    async def test_create_for_corpus_uses_configured_dimensions(self) -> None:
        store = self._make_mock_vector_store()
        manager = self._make_manager(vector_store=store, embedding_dimensions=768)
        await manager.create_for_corpus("corpus_xyz")
        store.create_collection.assert_called_once_with("corpus_xyz", 768)

    @pytest.mark.asyncio
    async def test_delete_for_corpus_calls_delete_collection(self) -> None:
        store = self._make_mock_vector_store()
        manager = self._make_manager(vector_store=store)
        await manager.delete_for_corpus("corpus_abc123")
        store.delete_collection.assert_called_once_with("corpus_abc123")

    @pytest.mark.asyncio
    async def test_ensure_exists_creates_if_missing(self) -> None:
        store = self._make_mock_vector_store()
        manager = self._make_manager(vector_store=store)
        await manager.ensure_exists("corpus_abc123")
        store.create_collection.assert_called_once_with("corpus_abc123", 1024)

    @pytest.mark.asyncio
    async def test_ensure_exists_idempotent(self) -> None:
        store = self._make_mock_vector_store()
        manager = self._make_manager(vector_store=store)
        await manager.ensure_exists("corpus_abc123")
        await manager.ensure_exists("corpus_abc123")
        assert store.create_collection.call_count == 2

    @pytest.mark.asyncio
    async def test_collection_name_format_validation(self) -> None:
        from callobuzz_rag.core.exceptions import VectorStoreError

        manager = self._make_manager()
        with pytest.raises(VectorStoreError, match="Collection name"):
            await manager.create_for_corpus("")
        with pytest.raises(VectorStoreError, match="Collection name"):
            await manager.create_for_corpus("   ")

    @pytest.mark.asyncio
    async def test_collection_name_invalid_chars_rejected(self) -> None:
        from callobuzz_rag.core.exceptions import VectorStoreError

        manager = self._make_manager()
        with pytest.raises(VectorStoreError, match="invalid characters"):
            await manager.create_for_corpus("corpus name with spaces")
        with pytest.raises(VectorStoreError, match="invalid characters"):
            await manager.create_for_corpus("corpus/path")
        with pytest.raises(VectorStoreError, match="invalid characters"):
            await manager.create_for_corpus("corpus.with.dots")

    @pytest.mark.asyncio
    async def test_collection_name_too_long_rejected(self) -> None:
        from callobuzz_rag.core.exceptions import VectorStoreError

        manager = self._make_manager()
        with pytest.raises(VectorStoreError, match="too long"):
            await manager.create_for_corpus("a" * 129)

    @pytest.mark.asyncio
    async def test_create_propagates_vector_store_errors(self) -> None:
        store = self._make_mock_vector_store()
        store.create_collection = AsyncMock(side_effect=RuntimeError("Qdrant unreachable"))
        manager = self._make_manager(vector_store=store)
        with pytest.raises(RuntimeError, match="Qdrant unreachable"):
            await manager.create_for_corpus("corpus_abc")


class TestAppLifespanCollectionChanges:
    def test_startup_no_default_collection_creation(self) -> None:
        """App lifespan must NOT create a default collection on startup."""
        from callobuzz_rag.api import app as app_module

        source = inspect.getsource(app_module.lifespan)
        assert "vector_store.create_collection" not in source, (
            "App lifespan must not directly call vector_store.create_collection. "
            "Use CollectionManager instead."
        )

    def test_startup_has_collection_manager(self) -> None:
        """App lifespan must create a CollectionManager on app.state."""
        from callobuzz_rag.api import app as app_module

        source = inspect.getsource(app_module.lifespan)
        assert "CollectionManager" in source
        assert "collection_manager" in source

    def test_startup_has_self_healing(self) -> None:
        """App lifespan must include self-healing loop for existing corpora."""
        from callobuzz_rag.api import app as app_module

        source = inspect.getsource(app_module.lifespan)
        assert "ensure_exists" in source
        assert "Corpus" in source or "corpora" in source.lower()
