"""Unit tests for migration context — dual-write seam."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.migration.context import DualWriteTarget, get_migration_context


@pytest.fixture
def mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.embedding_provider = "local"
    settings.embedding_model = "BAAI/bge-m3"
    settings.embedding_dimensions = 1024
    return settings


class TestGetMigrationContext:
    @pytest.mark.asyncio
    async def test_no_active_migration_returns_none(self, mock_settings: MagicMock) -> None:
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        result = await get_migration_context(session, uuid.uuid4(), mock_settings)
        assert result is None

    @pytest.mark.asyncio
    async def test_active_migration_returns_target(self, mock_settings: MagicMock) -> None:
        migration = MagicMock()
        migration.to_config = {
            "provider": "jina",
            "model": "jina-embeddings-v3",
            "dimensions": 1024,
        }
        migration.new_collection = "corpus_abc12345_v1"

        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = migration
        session.execute = AsyncMock(return_value=result_mock)

        with patch("callobuzz_rag.embeddings.factory.create_embedding_provider") as mock_factory:
            mock_embedder = MagicMock()
            mock_factory.return_value = mock_embedder

            result = await get_migration_context(session, uuid.uuid4(), mock_settings)

        assert result is not None
        assert isinstance(result, DualWriteTarget)
        assert result.collection == "corpus_abc12345_v1"
        assert result.model == "jina-embeddings-v3"
        assert result.embedder is mock_embedder


class TestGetDualWriteCollection:
    async def test_open_window_names_the_new_collection(self) -> None:
        from callobuzz_rag.migration.context import get_dual_write_collection

        migration = MagicMock()
        migration.new_collection = "corpus_abc12345_v1"
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = migration
        session.execute = AsyncMock(return_value=result_mock)
        assert await get_dual_write_collection(session, uuid.uuid4()) == "corpus_abc12345_v1"

    async def test_no_window_is_none(self) -> None:
        from callobuzz_rag.migration.context import get_dual_write_collection

        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)
        assert await get_dual_write_collection(session, uuid.uuid4()) is None
