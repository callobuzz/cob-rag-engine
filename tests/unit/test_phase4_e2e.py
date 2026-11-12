"""End-to-end tests for Phase 4 (Shared Multi-Corpus Engine).

Verifies auth enforcement, key isolation, endpoint routing, and
secrets module correctness.
"""

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

# --- Unit smoke tests: secrets module ---


class TestSecretsKeyGeneration:
    """Verify API key generation and verification."""

    def test_generate_and_verify_key(self) -> None:
        from callobuzz_rag.core.secrets import generate_api_key, hash_api_key, verify_api_key

        key = generate_api_key(prefix="rag")
        hashed = hash_api_key(key)
        assert verify_api_key(key, hashed) is True
        assert verify_api_key("wrong_key", hashed) is False

    def test_key_has_correct_prefix(self) -> None:
        from callobuzz_rag.core.secrets import generate_api_key

        key = generate_api_key(prefix="rag")
        assert key.startswith("rag_")

    def test_key_isolation_across_corpora(self) -> None:
        from callobuzz_rag.core.secrets import generate_api_key, hash_api_key, verify_api_key

        key_a = generate_api_key(prefix="rag")
        key_b = generate_api_key(prefix="rag")
        hash_a = hash_api_key(key_a)
        hash_b = hash_api_key(key_b)
        assert verify_api_key(key_a, hash_a) is True
        assert verify_api_key(key_a, hash_b) is False
        assert verify_api_key(key_b, hash_b) is True

    def test_key_rotation_invalidates_old_key(self) -> None:
        from callobuzz_rag.core.secrets import generate_api_key, hash_api_key, verify_api_key

        key_v1 = generate_api_key(prefix="rag")
        key_v2 = generate_api_key(prefix="rag")
        hash_v2 = hash_api_key(key_v2)
        assert verify_api_key(key_v1, hash_v2) is False
        assert verify_api_key(key_v2, hash_v2) is True

    def test_constant_time_compare(self) -> None:
        from callobuzz_rag.core.secrets import constant_time_compare

        assert constant_time_compare("admin_key", "admin_key") is True
        assert constant_time_compare("admin_key", "wrong") is False

    def test_fernet_key_generation(self) -> None:
        from callobuzz_rag.core.secrets import generate_fernet_key

        key = generate_fernet_key()
        assert isinstance(key, str)
        assert len(key) > 20


class TestFernetEncryption:
    """Verify Fernet encryption roundtrip."""

    def test_encrypt_decrypt_roundtrip(self) -> None:
        from callobuzz_rag.core.secrets import FernetEncryptor, generate_fernet_key

        key = generate_fernet_key()
        encryptor = FernetEncryptor(key)
        plaintext = "sk-test-api-key-12345"
        encrypted = encryptor.encrypt(plaintext)
        assert encrypted != plaintext
        decrypted = encryptor.decrypt(encrypted)
        assert decrypted == plaintext

    def test_decrypt_with_wrong_key_raises(self) -> None:
        from callobuzz_rag.core.secrets import (
            EncryptionError,
            FernetEncryptor,
            generate_fernet_key,
        )

        key1 = generate_fernet_key()
        key2 = generate_fernet_key()
        enc1 = FernetEncryptor(key1)
        enc2 = FernetEncryptor(key2)
        token = enc1.encrypt("secret")
        with pytest.raises(EncryptionError):
            enc2.decrypt(token)

    def test_invalid_fernet_key_raises(self) -> None:
        from callobuzz_rag.core.secrets import EncryptionError, FernetEncryptor

        with pytest.raises(EncryptionError):
            FernetEncryptor("not-a-valid-key")


# --- Auth middleware integration tests ---


class TestAuthMiddlewareScoping:
    """Verify require_admin and get_authenticated_corpus behavior."""

    def test_require_admin_rejects_no_auth(self) -> None:
        from callobuzz_rag.api.deps import require_admin

        request = MagicMock()
        request.state = MagicMock(spec=[])  # No is_admin attr

        with pytest.raises(HTTPException) as exc:
            require_admin(request)
        assert exc.value.status_code == 401

    def test_require_admin_rejects_non_admin(self) -> None:
        from callobuzz_rag.api.deps import require_admin

        request = MagicMock()
        request.state.is_admin = False

        with pytest.raises(HTTPException) as exc:
            require_admin(request)
        assert exc.value.status_code == 403

    def test_require_admin_allows_admin(self) -> None:
        from callobuzz_rag.api.deps import require_admin

        request = MagicMock()
        request.state.is_admin = True

        # Should not raise
        require_admin(request)

    def test_get_authenticated_corpus_rejects_admin_key(self) -> None:
        from callobuzz_rag.api.deps import get_authenticated_corpus

        request = MagicMock()
        request.state.is_admin = True
        request.state.corpus = None

        with pytest.raises(HTTPException) as exc:
            get_authenticated_corpus(request)
        assert exc.value.status_code == 403

    def test_get_authenticated_corpus_rejects_no_auth(self) -> None:
        from callobuzz_rag.api.deps import get_authenticated_corpus

        request = MagicMock()
        request.state = MagicMock(spec=[])  # No is_admin attr

        with pytest.raises(HTTPException) as exc:
            get_authenticated_corpus(request)
        assert exc.value.status_code == 401

    def test_get_authenticated_corpus_returns_corpus(self) -> None:
        from callobuzz_rag.api.deps import get_authenticated_corpus

        corpus = MagicMock()
        corpus.id = uuid.uuid4()
        request = MagicMock()
        request.state.is_admin = False
        request.state.corpus = corpus

        result = get_authenticated_corpus(request)
        assert result.id == corpus.id


# --- Config resolver integration ---


class TestConfigResolverIntegration:
    """Verify 3-tier config resolution works end-to-end."""

    @staticmethod
    def _make_settings() -> MagicMock:
        settings = MagicMock()
        settings.llm_model = "gpt-4"
        settings.llm_api_key = MagicMock()
        settings.llm_api_key.get_secret_value.return_value = "sk-default"
        settings.llm_api_base = "https://api.openai.com"
        settings.llm_temperature = 0.1
        settings.llm_max_tokens = 1024
        return settings

    def test_resolve_llm_defaults_only(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        resolver = ConfigResolver(self._make_settings())
        config = resolver.resolve_llm()
        assert config.model == "gpt-4"
        assert config.api_key == "sk-default"
        assert config.temperature == 0.1

    def test_resolve_llm_corpus_overrides(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        resolver = ConfigResolver(self._make_settings())
        config = resolver.resolve_llm(corpus_llm_config={"model": "claude-3"})
        assert config.model == "claude-3"
        assert config.api_key == "sk-default"  # Falls through

    def test_resolve_llm_header_overrides(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        resolver = ConfigResolver(self._make_settings())
        config = resolver.resolve_llm(
            request_headers={"X-LLM-Model": "gpt-4-turbo"},
            corpus_llm_config={"model": "claude-3"},
        )
        assert config.model == "gpt-4-turbo"  # Header wins

    def test_resolve_prompt_defaults(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        resolver = ConfigResolver(self._make_settings())
        config = resolver.resolve_prompt()
        assert config.system_prompt is not None
        assert config.constraints is not None

    def test_resolve_prompt_corpus_override(self) -> None:
        from callobuzz_rag.core.config_resolver import ConfigResolver

        resolver = ConfigResolver(self._make_settings())
        config = resolver.resolve_prompt(
            corpus_prompt_config={"system_prompt": "You are a legal expert."}
        )
        assert config.system_prompt == "You are a legal expert."


# --- Collection manager integration ---


class TestCollectionManagerValidation:
    """Verify CollectionManager validates names correctly."""

    def test_rejects_empty_name(self) -> None:
        from callobuzz_rag.core.exceptions import VectorStoreError
        from callobuzz_rag.vectordb.collection_manager import CollectionManager

        vector_store = MagicMock()
        manager = CollectionManager(vector_store=vector_store, embedding_dimensions=1024)

        with pytest.raises(VectorStoreError):
            manager._validate_collection_name("")

    def test_rejects_name_with_spaces(self) -> None:
        from callobuzz_rag.core.exceptions import VectorStoreError
        from callobuzz_rag.vectordb.collection_manager import CollectionManager

        vector_store = MagicMock()
        manager = CollectionManager(vector_store=vector_store, embedding_dimensions=1024)

        with pytest.raises(VectorStoreError):
            manager._validate_collection_name("invalid name with spaces!")

    def test_rejects_overly_long_name(self) -> None:
        from callobuzz_rag.core.exceptions import VectorStoreError
        from callobuzz_rag.vectordb.collection_manager import CollectionManager

        vector_store = MagicMock()
        manager = CollectionManager(vector_store=vector_store, embedding_dimensions=1024)

        with pytest.raises(VectorStoreError):
            manager._validate_collection_name("a" * 200)

    def test_accepts_valid_name(self) -> None:
        from callobuzz_rag.vectordb.collection_manager import CollectionManager

        vector_store = MagicMock()
        manager = CollectionManager(vector_store=vector_store, embedding_dimensions=1024)

        # Should not raise
        manager._validate_collection_name("corpus_abc-123")


# --- Corpus model field tests ---


class TestCorpusModelFields:
    """Verify Corpus model has all Phase 4 columns declared."""

    def test_corpus_has_api_key_hash(self) -> None:
        from callobuzz_rag.db.models.corpus import Corpus

        assert hasattr(Corpus, "api_key_hash")

    def test_corpus_has_qdrant_collection(self) -> None:
        from callobuzz_rag.db.models.corpus import Corpus

        assert hasattr(Corpus, "qdrant_collection")

    def test_corpus_has_llm_config(self) -> None:
        from callobuzz_rag.db.models.corpus import Corpus

        assert hasattr(Corpus, "llm_config")

    def test_corpus_has_prompt_config(self) -> None:
        from callobuzz_rag.db.models.corpus import Corpus

        assert hasattr(Corpus, "prompt_config")


class TestIngestJobCorpusScoping:
    """Verify IngestJob model has corpus_id column."""

    def test_ingest_job_has_corpus_id(self) -> None:
        from callobuzz_rag.db.models.ingest_job import IngestJob

        assert hasattr(IngestJob, "corpus_id")
