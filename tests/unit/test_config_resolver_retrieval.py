"""Tests for retrieval config resolution in ConfigResolver."""

from callobuzz_rag.core.config import Settings


def _make_settings(**overrides) -> Settings:
    defaults = {
        "_env_file": None,
        "jina_api_key": "test",
        "llm_api_key": "test",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_resolve_retrieval_returns_dataclass():
    """resolve_retrieval should return a ResolvedRetrievalConfig."""
    from callobuzz_rag.core.config_resolver import ConfigResolver, ResolvedRetrievalConfig

    settings = _make_settings()
    resolver = ConfigResolver(settings)
    result = resolver.resolve_retrieval()
    assert isinstance(result, ResolvedRetrievalConfig)


def test_resolve_retrieval_engine_defaults():
    """With no corpus config, should return engine defaults from Settings."""
    from callobuzz_rag.core.config_resolver import ConfigResolver

    settings = _make_settings(query_top_k=20, mmr_enabled=True, validation_enabled=True)
    resolver = ConfigResolver(settings)
    result = resolver.resolve_retrieval()
    assert result.top_k == 20
    assert result.mmr_enabled is True
    assert result.validation_enabled is True


def test_resolve_retrieval_corpus_overrides():
    """Corpus retrieval_config should override engine defaults."""
    from callobuzz_rag.core.config_resolver import ConfigResolver

    settings = _make_settings(query_top_k=20, mmr_enabled=True)
    resolver = ConfigResolver(settings)
    result = resolver.resolve_retrieval(
        corpus_retrieval_config={"top_k": 5, "mmr_enabled": False},
    )
    assert result.top_k == 5
    assert result.mmr_enabled is False


def test_resolve_retrieval_partial_corpus_config():
    """Corpus config with only some fields should use engine defaults for the rest."""
    from callobuzz_rag.core.config_resolver import ConfigResolver

    settings = _make_settings(query_top_k=20, validation_enabled=True)
    resolver = ConfigResolver(settings)
    result = resolver.resolve_retrieval(
        corpus_retrieval_config={"top_k": 8},
    )
    assert result.top_k == 8
    assert result.validation_enabled is True


def test_resolve_retrieval_cache_enabled_default():
    """cache_enabled should default to True (query cache on by default)."""
    from callobuzz_rag.core.config_resolver import ConfigResolver

    settings = _make_settings()
    resolver = ConfigResolver(settings)
    result = resolver.resolve_retrieval()
    assert result.cache_enabled is True


def test_resolve_retrieval_corpus_disables_cache():
    """Corpus can disable query cache."""
    from callobuzz_rag.core.config_resolver import ConfigResolver

    settings = _make_settings()
    resolver = ConfigResolver(settings)
    result = resolver.resolve_retrieval(
        corpus_retrieval_config={"cache_enabled": False},
    )
    assert result.cache_enabled is False
