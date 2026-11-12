"""A corpus is embedded and searched with the model its collection holds (G-17 and a fix).

- `POST /corpora` accepts `embedding_config` (validated like PUT) and builds the
  collection with that model's dimensions; `applied_embedding` is always set, which
  it never was, so the nightly drift check queued a no-op re-embed for every corpus.
- Ingest, query, conversations, memories and restore embedded with the engine
  default even when a corpus's collection held another model's vectors (after a
  migration), so such a corpus could not be searched.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.secrets import FernetEncryptor, generate_fernet_key
from callobuzz_rag.embeddings.factory import (
    clear_provider_cache,
    corpus_embedding_config,
    embedder_for_corpus,
)
from tests.unit.scope_harness import call, scoped_app

HOSTED = {"provider": "litellm", "model": "openai/text-embedding-3-small", "dimensions": 1536}


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_provider_cache()
    yield
    clear_provider_cache()


def _settings(**kw) -> Settings:
    return Settings(
        _env_file=None,
        embedding_provider="tei",
        embedding_model="BAAI/bge-base-en-v1.5",
        embedding_dimensions=768,
        **kw,
    )


def _corpus(applied=None, config=None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), applied_embedding=applied, embedding_config=config)


# --- create -------------------------------------------------------------------


def _creating_session(world) -> None:
    def flush_assigns_id():
        for call_ in world.session.add.call_args_list:
            obj = call_.args[0]
            obj.id = obj.id or uuid.uuid4()
            obj.created_at = datetime(2026, 9, 30, tzinfo=UTC)

    world.session.flush = AsyncMock(side_effect=flush_assigns_id)


async def test_create_with_embedding_model_builds_its_collection() -> None:
    with scoped_app() as (app, world):
        _creating_session(world)
        res = await call(
            app,
            "admin",
            "POST",
            "/api/v1/corpora",
            json={
                "name": "Hosted",
                "embedding_config": {"model": HOSTED["model"]},
                "tenant_id": str(world.tenant_a.id),
            },
        )
        created = world.session.add.call_args_list[0].args[0]  # the corpus (its key follows)
        dims = app.state.vector_store.create_collection.await_args.args[1]
    assert res.status_code == 201, res.text
    assert created.applied_embedding == HOSTED
    assert created.embedding_config == HOSTED
    assert dims == 1536


async def test_create_without_embedding_model_records_the_engine_default() -> None:
    with scoped_app() as (app, world):
        _creating_session(world)
        s = app.state.settings
        res = await call(app, "tk_a", "POST", "/api/v1/corpora", json={"name": "Plain"})
        created = world.session.add.call_args_list[0].args[0]  # the corpus (its key follows)
        dims = app.state.vector_store.create_collection.await_args.args[1]
    assert res.status_code == 201, res.text
    assert created.applied_embedding == {
        "provider": s.embedding_provider,
        "model": s.embedding_model,
        "dimensions": s.embedding_dimensions,
    }
    assert created.embedding_config is None
    assert dims == s.embedding_dimensions


async def test_create_with_unknown_model_is_422() -> None:
    with scoped_app() as (app, world):
        _creating_session(world)
        res = await call(
            app,
            "admin",
            "POST",
            "/api/v1/corpora",
            json={
                "name": "X",
                "embedding_config": {"model": "nobody/unknown"},
                "tenant_id": str(world.tenant_a.id),
            },
        )
    assert res.status_code == 422
    assert "code" not in res.json()  # the model check, not tenant_required
    world.session.add.assert_not_called()


async def test_create_encrypts_the_embedding_key() -> None:
    key = generate_fernet_key()
    with scoped_app() as (app, world):
        app.state.settings.encryption_key = key
        _creating_session(world)
        res = await call(
            app,
            "admin",
            "POST",
            "/api/v1/corpora",
            json={
                "name": "Hosted",
                "embedding_config": {"model": HOSTED["model"], "api_key": "sk-emb"},
                "tenant_id": str(world.tenant_a.id),
            },
        )
        created = world.session.add.call_args_list[0].args[0]  # the corpus (its key follows)
    assert res.status_code == 201, res.text
    assert "api_key" not in created.embedding_config
    assert FernetEncryptor(key).decrypt(created.embedding_config["api_key_encrypted"]) == "sk-emb"


# --- runtime embedder -----------------------------------------------------------


def test_engine_default_collection_uses_the_default_embedder() -> None:
    s = _settings()
    default = object()
    for applied in (None, {"provider": "tei", "model": "BAAI/bge-base-en-v1.5", "dimensions": 768}):
        corpus = _corpus(applied)
        assert corpus_embedding_config(corpus, s) is None
        assert embedder_for_corpus(corpus, s, default) is default  # type: ignore[arg-type]


def test_other_model_collection_gets_its_own_embedder_with_its_key() -> None:
    key = generate_fernet_key()
    s = _settings(encryption_key=key)
    corpus = _corpus(
        HOSTED, {**HOSTED, "api_key_encrypted": FernetEncryptor(key).encrypt("sk-corpus")}
    )
    resolved = corpus_embedding_config(corpus, s)
    assert resolved is not None
    assert (resolved.provider, resolved.model, resolved.dimensions) == (
        "litellm",
        HOSTED["model"],
        1536,
    )
    assert resolved.api_key == "sk-corpus"
    embedder = embedder_for_corpus(corpus, s, object())  # type: ignore[arg-type]
    assert embedder.model_id == HOSTED["model"]
    # Cached per config in the API process; a worker asks for a fresh one.
    assert embedder_for_corpus(corpus, s, object()) is embedder  # type: ignore[arg-type]
    assert embedder_for_corpus(corpus, s, object(), cache=False) is not embedder  # type: ignore[arg-type]


def test_key_for_another_model_is_not_used() -> None:
    key = generate_fernet_key()
    s = _settings(encryption_key=key)
    stale = {
        "model": "openai/text-embedding-3-large",
        "api_key_encrypted": FernetEncryptor(key).encrypt("x"),
    }
    resolved = corpus_embedding_config(_corpus(HOSTED, stale), s)
    assert resolved is not None and resolved.api_key is None


async def test_query_orchestrator_embeds_with_the_corpus_model() -> None:
    from callobuzz_rag.api.routes.query import _build_orchestrator
    from callobuzz_rag.core.config_resolver import ConfigResolver

    s = _settings()
    request = MagicMock()
    request.app.state.settings = s
    request.app.state.embedder = default = MagicMock()
    request.app.state.reranker = None
    corpus = _corpus(HOSTED)
    corpus.qdrant_collection = "c"
    resolver = ConfigResolver(s)
    orch = _build_orchestrator(
        request, AsyncMock(), corpus, resolver.resolve_llm(), resolver.resolve_prompt()
    )
    assert orch._embedder is not default
    assert orch._embedder.model_id == HOSTED["model"]


async def test_search_cache_is_keyed_by_the_providers_model() -> None:
    from callobuzz_rag.retrieval.search import HybridSearcher

    provider = MagicMock()
    provider.model_id = "openai/text-embedding-3-small"
    cache = MagicMock()
    cache.get_or_embed = AsyncMock(side_effect=RuntimeError("stop here"))
    searcher = HybridSearcher(
        vector_store=MagicMock(),
        embedding_provider=provider,
        embedding_cache=cache,
        session=AsyncMock(),
        settings=_settings(),
        collection="c",
    )
    with pytest.raises(Exception):  # noqa: B017 - only the cache call matters here
        await searcher.search("q", MagicMock(), top_k_dense=5)
    assert cache.get_or_embed.await_args.kwargs["model"] == "openai/text-embedding-3-small"


async def test_worker_swaps_in_and_closes_properly() -> None:
    from callobuzz_rag.worker.tasks import _use_corpus_embedder

    s = _settings()
    corpus = _corpus(HOSTED)
    session = AsyncMock()
    session.get = AsyncMock(return_value=corpus)
    default = AsyncMock()
    providers = {"embedder": default}
    await _use_corpus_embedder(session, s, providers, str(corpus.id))
    default.close.assert_awaited_once()
    assert providers["embedder"].model_id == HOSTED["model"]

    plain = {"embedder": default}
    session.get = AsyncMock(return_value=_corpus(None))
    await _use_corpus_embedder(session, s, plain, str(uuid.uuid4()))
    assert plain["embedder"] is default


async def test_migration_probe_uses_the_corpus_key() -> None:
    from callobuzz_rag.migration.strategies import preflight_probe

    fake = AsyncMock()
    fake.embed_texts = AsyncMock(return_value=[SimpleNamespace(dimensions=1536)])
    with patch(
        "callobuzz_rag.migration.strategies.create_embedding_provider", return_value=fake
    ) as make:
        await preflight_probe(_settings(), HOSTED, api_key="sk-corpus")
    resolved = make.call_args.kwargs["resolved"]
    assert resolved.api_key == "sk-corpus"
    assert make.call_args.kwargs["cache"] is False
