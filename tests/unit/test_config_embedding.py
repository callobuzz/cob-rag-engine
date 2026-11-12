"""Tests for embedding provider config settings."""

import pytest


class TestEmbeddingProviderConfig:
    def test_default_provider_is_local(self) -> None:
        from callobuzz_rag.core.config import Settings

        settings = Settings(_env_file=None)
        assert settings.embedding_provider == "local"

    def test_default_model_is_bge_m3(self) -> None:
        from callobuzz_rag.core.config import Settings

        settings = Settings(_env_file=None)
        assert settings.embedding_model == "BAAI/bge-m3"

    def test_default_dimensions_is_1024(self) -> None:
        from callobuzz_rag.core.config import Settings

        settings = Settings(_env_file=None)
        assert settings.embedding_dimensions == 1024

    def test_default_tei_url(self) -> None:
        from callobuzz_rag.core.config import Settings

        settings = Settings(_env_file=None)
        # IPv4 literal, not the name `localhost`: on Windows + Docker Desktop
        # `localhost` resolves to ::1 where wslrelay.exe resets the connection.
        assert settings.tei_url == "http://127.0.0.1:8081"

    def test_provider_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "tei")
        from callobuzz_rag.core.config import Settings

        settings = Settings()
        assert settings.embedding_provider == "tei"

    def test_provider_jina_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "jina")
        from callobuzz_rag.core.config import Settings

        settings = Settings()
        assert settings.embedding_provider == "jina"
