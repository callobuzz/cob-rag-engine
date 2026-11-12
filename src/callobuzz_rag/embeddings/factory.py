"""Config-driven embedding provider factory with per-config instance caching.

Shared by api/app.py lifespan and worker/tasks.py provider creation.
"""

import hashlib
import logging
from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.core.config_resolver import ResolvedEmbeddingConfig
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.embeddings.base import EmbeddingProvider

logger = logging.getLogger(__name__)

_provider_cache: dict[tuple[str, str, str], "EmbeddingProvider"] = {}


def _key_fingerprint(api_key: str | None) -> str:
    if not api_key:
        return "default"
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def _build_provider(provider: str, settings: "Settings") -> "EmbeddingProvider":
    provider = provider.lower()
    if provider == "local":
        from callobuzz_rag.embeddings.bge_m3 import BgeM3EmbeddingProvider

        return BgeM3EmbeddingProvider(settings)
    elif provider == "tei":
        from callobuzz_rag.embeddings.tei import TEIEmbeddingProvider

        return TEIEmbeddingProvider(settings)
    elif provider == "jina":
        from callobuzz_rag.embeddings.jina import JinaEmbeddingProvider

        return JinaEmbeddingProvider(settings)
    elif provider == "litellm":
        from callobuzz_rag.embeddings.litellm_embed import LiteLLMEmbeddingProvider

        return LiteLLMEmbeddingProvider(settings)
    else:
        raise ValueError(
            f"Unknown embedding provider: '{provider}'. Valid options: local, tei, jina, litellm"
        )


def create_embedding_provider(
    settings: "Settings",
    resolved: "ResolvedEmbeddingConfig | None" = None,
    *,
    cache: bool = True,
) -> "EmbeddingProvider":
    """Create an embedding provider, optionally from a per-corpus resolved config.

    When resolved is None, uses global engine defaults (legacy behavior, no caching).
    When resolved is provided, instances are cached by (provider, model, key_fingerprint)
    unless `cache=False`. A worker task runs in its own `asyncio.run()`, so it must
    not share an instance (and its loop-bound HTTP client) with the next task: it
    passes `cache=False` and closes what it gets.
    """
    if resolved is None:
        return _build_provider(settings.embedding_provider, settings)

    cache_key = (resolved.provider, resolved.model, _key_fingerprint(resolved.api_key))
    cached = _provider_cache.get(cache_key) if cache else None
    if cached is not None:
        return cached

    from pydantic import SecretStr

    overrides: dict[str, object] = {
        "embedding_provider": resolved.provider,
        "embedding_model": resolved.model,
        "embedding_dimensions": resolved.dimensions,
    }
    if resolved.provider == "jina":
        overrides["jina_model"] = resolved.model
        overrides["jina_dimensions"] = resolved.dimensions
        if resolved.api_key:
            overrides["jina_api_key"] = SecretStr(resolved.api_key)
    elif resolved.provider == "litellm" and resolved.api_key:
        overrides["embedding_api_key"] = SecretStr(resolved.api_key)

    settings_copy = settings.model_copy(update=overrides)
    instance = _build_provider(resolved.provider, settings_copy)
    if cache:
        _provider_cache[cache_key] = instance
    return instance


def corpus_embedding_key(corpus: "Corpus", settings: "Settings", model: str) -> str | None:
    """The corpus's own hosted-embedding key, decrypted, if it is for `model`."""
    config = corpus.embedding_config or {}
    token = config.get("api_key_encrypted")
    if not token or config.get("model") != model:
        return None
    from callobuzz_rag.core.secrets import encryptor_from_settings

    try:
        encryptor = encryptor_from_settings(settings)
        return encryptor.decrypt(str(token)) if encryptor is not None else None
    except Exception:
        logger.warning("Could not decrypt the embedding key of corpus %s", corpus.id)
        return None


def corpus_embedding_config(
    corpus: "Corpus", settings: "Settings"
) -> "ResolvedEmbeddingConfig | None":
    """The model a corpus's live collection holds, or None when it is the engine default.

    `applied_embedding` records what the collection was built with (set at
    create, moved by a completed migration). Queries and new chunks must embed
    with that same model, whatever the engine default is today.
    """
    from callobuzz_rag.core.config_resolver import ResolvedEmbeddingConfig

    applied = corpus.applied_embedding or {}
    provider = str(applied.get("provider") or settings.embedding_provider)
    model = str(applied.get("model") or settings.embedding_model)
    if provider == settings.embedding_provider and model == settings.embedding_model:
        return None
    return ResolvedEmbeddingConfig(
        provider=provider,
        model=model,
        dimensions=int(applied.get("dimensions") or settings.embedding_dimensions),
        api_key=corpus_embedding_key(corpus, settings, model),
    )


@overload
def embedder_for_corpus(
    corpus: "Corpus",
    settings: "Settings",
    default: "EmbeddingProvider",
    *,
    cache: bool = True,
) -> "EmbeddingProvider": ...


@overload
def embedder_for_corpus(
    corpus: "Corpus",
    settings: "Settings",
    default: None,
    *,
    cache: bool = True,
) -> "EmbeddingProvider | None": ...


def embedder_for_corpus(
    corpus: "Corpus",
    settings: "Settings",
    default: "EmbeddingProvider | None",
    *,
    cache: bool = True,
) -> "EmbeddingProvider | None":
    """The embedder that matches the corpus's collection: `default`, or its own model's."""
    resolved = corpus_embedding_config(corpus, settings)
    if resolved is None:
        return default
    return create_embedding_provider(settings, resolved, cache=cache)


def clear_provider_cache() -> None:
    """Clear the instance cache. Useful in tests."""
    _provider_cache.clear()
