"""A corpus's stored LLM / embedding key is decrypted where it is resolved.

The query path read `app.state.encryptor`, which nothing set, and the migration
service built its resolver with no encryptor at all: both fell through to the
engine default key, so a corpus's own key was never used.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.secrets import (
    FernetEncryptor,
    encryptor_from_settings,
    generate_fernet_key,
)


def _settings(key: str = "") -> Settings:
    return Settings(encryption_key=key, llm_api_key="engine-default-key")


def test_encryptor_from_settings_none_without_key() -> None:
    assert encryptor_from_settings(_settings("")) is None
    assert encryptor_from_settings(_settings(" , ")) is None


def test_encryptor_from_settings_round_trips() -> None:
    key = generate_fernet_key()
    enc = encryptor_from_settings(_settings(key))
    assert enc is not None
    assert FernetEncryptor(key).decrypt(enc.encrypt("secret")) == "secret"


def test_query_resolver_decrypts_corpus_llm_key_without_state_encryptor() -> None:
    from callobuzz_rag.api.routes.query import _get_config_resolver

    key = generate_fernet_key()
    settings = _settings(key)
    token = FernetEncryptor(key).encrypt("corpus-own-key")
    request = MagicMock()
    request.app.state = SimpleNamespace(settings=settings)

    resolved = _get_config_resolver(request).resolve_llm(
        request_headers=None, corpus_llm_config={"api_key_encrypted": token}
    )
    assert resolved.api_key == "corpus-own-key"


def test_query_resolver_uses_state_encryptor() -> None:
    from callobuzz_rag.api.routes.query import _get_config_resolver

    key = generate_fernet_key()
    settings = _settings(key)
    token = FernetEncryptor(key).encrypt("corpus-own-key")
    request = MagicMock()
    request.app.state = SimpleNamespace(
        settings=settings, encryptor=encryptor_from_settings(settings)
    )
    resolved = _get_config_resolver(request).resolve_llm(
        request_headers=None, corpus_llm_config={"api_key_encrypted": token}
    )
    assert resolved.api_key == "corpus-own-key"


def test_migration_desired_config_decrypts_corpus_embedding_key() -> None:
    from callobuzz_rag.migration.service import effective_desired

    key = generate_fernet_key()
    settings = _settings(key)
    corpus = SimpleNamespace(
        embedding_config={
            "provider": "litellm",
            "model": "openai/text-embedding-3-small",
            "api_key_encrypted": FernetEncryptor(key).encrypt("emb-key"),
        }
    )
    desired = effective_desired(corpus, settings)  # type: ignore[arg-type]
    assert desired.api_key == "emb-key"
