"""Tests for config-driven embedding provider selection in app lifespan."""

from unittest.mock import MagicMock, patch

import pytest


class TestEmbeddingProviderSelection:
    def test_local_provider_selected(self) -> None:
        """When RAG_EMBEDDING_PROVIDER=local, BgeM3EmbeddingProvider is used."""
        from callobuzz_rag.embeddings.factory import (
            create_embedding_provider as _create_embedding_provider,
        )

        settings = MagicMock()
        settings.embedding_provider = "local"
        settings.embedding_model = "BAAI/bge-m3"
        settings.embedding_dimensions = 1024

        with patch("callobuzz_rag.embeddings.bge_m3.BgeM3EmbeddingProvider") as mock_cls:
            mock_cls.return_value = MagicMock()
            _create_embedding_provider(settings)
            mock_cls.assert_called_once_with(settings)

    def test_tei_provider_selected(self) -> None:
        """When RAG_EMBEDDING_PROVIDER=tei, TEIEmbeddingProvider is used."""
        from callobuzz_rag.embeddings.factory import (
            create_embedding_provider as _create_embedding_provider,
        )

        settings = MagicMock()
        settings.embedding_provider = "tei"
        settings.tei_url = "http://localhost:8081"
        settings.embedding_model = "BAAI/bge-m3"
        settings.embedding_dimensions = 1024

        with patch("callobuzz_rag.embeddings.tei.TEIEmbeddingProvider") as mock_cls:
            mock_cls.return_value = MagicMock()
            _create_embedding_provider(settings)
            mock_cls.assert_called_once_with(settings)

    def test_jina_provider_selected(self) -> None:
        """When RAG_EMBEDDING_PROVIDER=jina, JinaEmbeddingProvider is used."""
        from callobuzz_rag.embeddings.factory import (
            create_embedding_provider as _create_embedding_provider,
        )

        settings = MagicMock()
        settings.embedding_provider = "jina"

        with patch("callobuzz_rag.embeddings.jina.JinaEmbeddingProvider") as mock_cls:
            mock_cls.return_value = MagicMock()
            _create_embedding_provider(settings)
            mock_cls.assert_called_once_with(settings)

    def test_unknown_provider_raises(self) -> None:
        """Unknown provider value raises ValueError."""
        from callobuzz_rag.embeddings.factory import (
            create_embedding_provider as _create_embedding_provider,
        )

        settings = MagicMock()
        settings.embedding_provider = "openai"

        with pytest.raises(ValueError, match="Unknown embedding provider"):
            _create_embedding_provider(settings)
