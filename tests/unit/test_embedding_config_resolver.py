"""Unit tests for ResolvedEmbeddingConfig and resolve_embedding."""

from unittest.mock import MagicMock

import pytest

from callobuzz_rag.core.config_resolver import ConfigResolver


@pytest.fixture
def mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.embedding_provider = "local"
    settings.embedding_model = "BAAI/bge-m3"
    settings.embedding_dimensions = 1024
    return settings


class TestResolveEmbedding:
    def test_engine_defaults_when_no_corpus_config(self, mock_settings: MagicMock) -> None:
        resolver = ConfigResolver(mock_settings)
        resolved = resolver.resolve_embedding(None)
        assert resolved.provider == "local"
        assert resolved.model == "BAAI/bge-m3"
        assert resolved.dimensions == 1024
        assert resolved.api_key is None

    def test_corpus_overrides_engine_defaults(self, mock_settings: MagicMock) -> None:
        resolver = ConfigResolver(mock_settings)
        corpus_config = {"provider": "jina", "model": "jina-embeddings-v3"}
        resolved = resolver.resolve_embedding(corpus_config)
        assert resolved.provider == "jina"
        assert resolved.model == "jina-embeddings-v3"
        assert resolved.dimensions == 1024

    def test_invalid_model_raises(self, mock_settings: MagicMock) -> None:
        resolver = ConfigResolver(mock_settings)
        with pytest.raises(ValueError, match="No catalog entry"):
            resolver.resolve_embedding({"provider": "local", "model": "nonexistent"})

    def test_dimensions_from_catalog(self, mock_settings: MagicMock) -> None:
        resolver = ConfigResolver(mock_settings)
        resolved = resolver.resolve_embedding({"provider": "jina", "model": "jina-embeddings-v3"})
        assert resolved.dimensions == 1024

    def test_encrypted_key_decryption(self, mock_settings: MagicMock) -> None:
        encryptor = MagicMock()
        encryptor.decrypt.return_value = "decrypted-key"
        resolver = ConfigResolver(mock_settings, encryptor=encryptor)
        resolved = resolver.resolve_embedding(
            {"provider": "jina", "model": "jina-embeddings-v3", "api_key_encrypted": "enc123"}
        )
        assert resolved.api_key == "decrypted-key"
        encryptor.decrypt.assert_called_once_with("enc123")

    def test_encrypted_key_without_encryptor(self, mock_settings: MagicMock) -> None:
        resolver = ConfigResolver(mock_settings, encryptor=None)
        resolved = resolver.resolve_embedding(
            {"provider": "jina", "model": "jina-embeddings-v3", "api_key_encrypted": "enc123"}
        )
        assert resolved.api_key is None
