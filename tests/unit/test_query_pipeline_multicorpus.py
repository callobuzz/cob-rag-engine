"""Tests for Epic 4-07: Query Pipeline Integration (multi-corpus config resolution)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from callobuzz_rag.api.routes.query import (
    _extract_llm_headers,
    _resolve_corpus,
)
from callobuzz_rag.api.schemas.query import QueryRequest
from callobuzz_rag.core.config_resolver import ResolvedLLMConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_corpus(
    corpus_id: uuid.UUID | None = None,
    qdrant_collection: str = "corpus_chunks",
    llm_config: dict | None = None,
    prompt_config: dict | None = None,
) -> SimpleNamespace:
    """Create a fake Corpus-like object."""
    return SimpleNamespace(
        id=corpus_id or uuid.uuid4(),
        name="test-corpus",
        qdrant_collection=qdrant_collection,
        llm_config=llm_config,
        prompt_config=prompt_config,
    )


def _make_request(
    corpus: Any = None,
    is_admin: bool = False,
    headers: dict[str, str] | None = None,
    settings: Any = None,
    embedder: Any = "embedder",
    context_assembler: Any = "assembler",
    vector_store: Any = "vstore",
    llm_provider: Any = "llm",
    reranker: Any = None,
    config_resolver: Any = None,
    encryptor: Any = None,
) -> MagicMock:
    """Create a fake Request object with state and headers.

    `tenant` must be pinned to None. Left unset on a MagicMock it auto-creates a
    truthy child mock, so `_resolve_corpus` took the tenant branch and answered 422
    ("corpus_id is required when using a tenant API key") where the unauthenticated
    case should be 401.
    """
    request = MagicMock()
    request.state.corpus = corpus
    request.state.is_admin = is_admin
    request.state.tenant = None
    request.state.settings = settings or _make_settings()
    request.app.state.settings = request.state.settings
    request.app.state.embedder = embedder
    request.app.state.context_assembler = context_assembler
    request.app.state.vector_store = vector_store
    request.app.state.llm_provider = llm_provider
    request.app.state.reranker = reranker
    request.app.state.config_resolver = config_resolver
    request.app.state.encryptor = encryptor

    real_headers = headers or {}
    headers_mock = MagicMock()
    headers_mock.get = lambda key, default=None: real_headers.get(key, default)
    request.headers = headers_mock
    return request


def _make_settings(**overrides: Any) -> SimpleNamespace:
    """Create a fake Settings object."""
    defaults = {
        "qdrant_collection": "default_chunks",
        # Engine-level LLM defaults. Empty provider is the shipped production
        # setup: callers send X-LLM-Provider and the engine holds nothing.
        "llm_provider": "",
        "llm_model": "openai/gpt-4o-mini",
        "llm_api_key": MagicMock(get_secret_value=lambda: "default-key"),
        "llm_api_base": "https://api.openai.com/v1",
        "llm_temperature": 0.7,
        "llm_max_tokens": 4096,
        "llm_fast_model": None,
        "llm_fast_provider_order": [],
        "llm_thinking": None,
        "llm_fast_thinking": "off",
        "query_stream_enabled": True,
        "query_top_k": 10,
        "query_final_k": 5,
        "query_classification_enabled": False,
        "llm_fast_temperature": 0.0,
        "llm_fast_max_tokens": 512,
        "llm_timeout_seconds": 120.0,
        "llm_fast_timeout_seconds": 30.0,
        "hyde_enabled": False,
        "mmr_enabled": False,
        "temporal_decay_enabled": False,
        "rrf_k": 60,
        "rerank_top_k_output": 10,
        "mmr_lambda": 0.7,
        "temporal_decay_half_life_days": 30,
        "context_max_tokens": 4000,
        "response_reserve_tokens": 512,
        "validation_enabled": False,
        "multi_query_count": 3,
        "validation_gatekeeper_weight": 0.4,
        "validation_auditor_weight": 0.3,
        "validation_strategist_weight": 0.3,
        "validation_confidence_threshold": 0.5,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ===========================================================================
# TestResolveCorpus
# ===========================================================================


class TestResolveCorpus:
    """Tests for _resolve_corpus helper."""

    def test_auth_corpus_used(self) -> None:
        """When authenticated with corpus key, returns that corpus."""
        corpus = _make_corpus()
        request = _make_request(corpus=corpus)
        body = QueryRequest(query="hello")

        result = _resolve_corpus(request, body)
        assert result is corpus

    def test_matching_corpus_id(self) -> None:
        """When corpus_id in body matches auth corpus, returns auth corpus."""
        corpus = _make_corpus()
        request = _make_request(corpus=corpus)
        body = QueryRequest(query="hello", corpus_id=corpus.id)

        result = _resolve_corpus(request, body)
        assert result is corpus

    def test_mismatch_corpus_id_raises_403(self) -> None:
        """When corpus_id in body doesn't match auth corpus, raises 403."""
        corpus = _make_corpus()
        request = _make_request(corpus=corpus)
        body = QueryRequest(query="hello", corpus_id=uuid.uuid4())

        with pytest.raises(HTTPException) as exc_info:
            _resolve_corpus(request, body)
        assert exc_info.value.status_code == 403

    def test_admin_requires_corpus_id(self) -> None:
        """Admin key without corpus_id in body raises 422."""
        request = _make_request(is_admin=True)
        body = QueryRequest(query="hello")

        with pytest.raises(HTTPException) as exc_info:
            _resolve_corpus(request, body)
        assert exc_info.value.status_code == 422

    def test_admin_with_corpus_id_returns_none(self) -> None:
        """Admin key with corpus_id returns None (signals DB lookup)."""
        request = _make_request(is_admin=True)
        body = QueryRequest(query="hello", corpus_id=uuid.uuid4())

        result = _resolve_corpus(request, body)
        assert result is None

    def test_no_auth_raises_401(self) -> None:
        """No corpus and not admin raises 401."""
        request = _make_request()
        body = QueryRequest(query="hello")

        with pytest.raises(HTTPException) as exc_info:
            _resolve_corpus(request, body)
        assert exc_info.value.status_code == 401


# ===========================================================================
# TestQueryUsesCorpusConfig
# ===========================================================================


class TestQueryUsesCorpusConfig:
    """Tests that query uses corpus-specific configuration."""

    def test_qdrant_collection_from_corpus(self) -> None:
        """Orchestrator uses corpus.qdrant_collection, not default."""
        from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator

        settings = _make_settings()
        orch = QueryOrchestrator(
            session=MagicMock(),
            settings=settings,
            embedder=MagicMock(),
            vector_store=MagicMock(),
            llm_provider=MagicMock(),
            context_assembler=MagicMock(),
            collection_name="my_custom_collection",
        )
        assert orch._collection_name == "my_custom_collection"

    def test_qdrant_collection_defaults_to_settings(self) -> None:
        """Orchestrator falls back to settings.qdrant_collection."""
        from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator

        settings = _make_settings(qdrant_collection="default_col")
        orch = QueryOrchestrator(
            session=MagicMock(),
            settings=settings,
            embedder=MagicMock(),
            vector_store=MagicMock(),
            llm_provider=MagicMock(),
            context_assembler=MagicMock(),
        )
        assert orch._collection_name == "default_col"

    def test_llm_config_from_corpus(self) -> None:
        """Resolved LLM config uses corpus model override."""
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        resolver = ConfigResolver(settings)
        resolved = resolver.resolve_llm(corpus_llm_config={"model": "anthropic/claude-3-opus"})
        assert resolved.model == "anthropic/claude-3-opus"

    def test_system_prompt_from_corpus(self) -> None:
        """Resolved prompt config uses corpus system_prompt."""
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        resolver = ConfigResolver(settings)
        resolved = resolver.resolve_prompt(
            corpus_prompt_config={"system_prompt": "You are a legal assistant."}
        )
        assert resolved.system_prompt == "You are a legal assistant."

    def test_request_system_prompt_overrides_corpus(self) -> None:
        """Request-level system_prompt beats corpus-level."""
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        resolver = ConfigResolver(settings)
        resolved = resolver.resolve_prompt(
            request_body={"system_prompt": "Request override"},
            corpus_prompt_config={"system_prompt": "Corpus prompt"},
        )
        assert resolved.system_prompt == "Request override"


# ===========================================================================
# TestQueryWithResponseLanguage
# ===========================================================================


class TestQueryWithResponseLanguage:
    """Tests for response_language in resolved prompt."""

    def test_response_language_from_corpus(self) -> None:
        """Resolved prompt picks up response_language from corpus config."""
        from callobuzz_rag.core.config_resolver import ConfigResolver

        settings = _make_settings()
        resolver = ConfigResolver(settings)
        resolved = resolver.resolve_prompt(corpus_prompt_config={"response_language": "Spanish"})
        assert resolved.response_language == "Spanish"


# ===========================================================================
# TestStreamQueryCorpusConfig
# ===========================================================================


class TestStreamQueryCorpusConfig:
    """Tests that streaming query uses corpus configuration."""

    def test_orchestrator_has_llm_property(self) -> None:
        """Orchestrator exposes llm via public property."""
        from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator

        llm_mock = MagicMock()
        orch = QueryOrchestrator(
            session=MagicMock(),
            settings=_make_settings(),
            embedder=MagicMock(),
            vector_store=MagicMock(),
            llm_provider=llm_mock,
            context_assembler=MagicMock(),
            collection_name="stream_collection",
        )
        assert orch.llm is llm_mock


# ===========================================================================
# TestExtractLLMHeaders
# ===========================================================================


class TestExtractLLMHeaders:
    """Tests for _extract_llm_headers helper."""

    def test_extracts_llm_headers(self) -> None:
        """Extracts X-LLM-* headers from request."""
        request = _make_request(
            headers={
                "x-llm-model": "openai/gpt-4",
                "x-llm-api-key": "sk-override",
                "x-llm-base-url": "https://custom.api/v1",
            }
        )
        headers = _extract_llm_headers(request)
        assert headers == {
            "x-llm-model": "openai/gpt-4",
            "x-llm-api-key": "sk-override",
            "x-llm-base-url": "https://custom.api/v1",
        }

    def test_empty_when_no_headers(self) -> None:
        """Returns empty dict when no X-LLM-* headers present."""
        request = _make_request(headers={})
        headers = _extract_llm_headers(request)
        assert headers == {}


# ===========================================================================
# TestLiteLLMFromResolvedConfig
# ===========================================================================


class TestLiteLLMFromResolvedConfig:
    """Tests for LiteLLMProvider.from_resolved_config classmethod."""

    def test_from_resolved_config_creates_provider(self) -> None:
        """Creates LiteLLMProvider from ResolvedLLMConfig."""
        from callobuzz_rag.llm.litellm_provider import LiteLLMProvider

        config = ResolvedLLMConfig(
            model="openai/gpt-4o-mini",
            api_key="sk-test",
            base_url=None,
            temperature=0.5,
            max_tokens=1024,
        )
        provider = LiteLLMProvider.from_resolved_config(config)
        assert provider._model == "openai/gpt-4o-mini"
        assert provider._api_key == "sk-test"
        assert provider._api_base is None
        assert provider._temperature == 0.5
        assert provider._max_tokens == 1024
        # fast_model defaults to model when None
        assert provider._fast_model == "openai/gpt-4o-mini"

    def test_from_resolved_config_rejects_wrong_type(self) -> None:
        """Raises TypeError if not ResolvedLLMConfig."""
        from callobuzz_rag.llm.litellm_provider import LiteLLMProvider

        with pytest.raises(TypeError, match="Expected ResolvedLLMConfig"):
            LiteLLMProvider.from_resolved_config({"model": "test"})  # type: ignore[arg-type]
