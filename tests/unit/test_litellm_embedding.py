"""Hosted embeddings through LiteLLM (RAG_EMBEDDING_PROVIDER=litellm)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.config_resolver import ConfigResolver
from callobuzz_rag.embeddings.factory import _build_provider
from callobuzz_rag.embeddings.litellm_embed import LiteLLMEmbeddingProvider


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "embedding_provider": "litellm",
        "embedding_model": "openai/text-embedding-3-small",
        "embedding_dimensions": 3,
        "embedding_api_key": SecretStr("sk-test"),
        "embedding_batch_size": 2,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _response(n: int, dims: int = 3) -> SimpleNamespace:
    # Deliberately out of order: the provider must sort by index.
    data = [{"index": i, "embedding": [float(i)] * dims} for i in reversed(range(n))]
    return SimpleNamespace(data=data, usage=SimpleNamespace(prompt_tokens=7), model="m")


class TestLiteLLMEmbeddingProvider:
    async def test_batches_and_preserves_order(self) -> None:
        provider = LiteLLMEmbeddingProvider(_settings())
        mock = AsyncMock(side_effect=[_response(2), _response(1)])
        with patch("litellm.aembedding", mock):
            results = await provider.embed_texts(["a", "b", "c"])

        assert mock.await_count == 2  # batch size 2 -> two calls for three texts
        assert [r.vector[0] for r in results] == [0.0, 1.0, 0.0]
        first = mock.await_args_list[0].kwargs
        assert first["model"] == "openai/text-embedding-3-small"
        assert first["api_key"] == "sk-test"
        assert first["dimensions"] == 3
        assert first["drop_params"] is True
        assert "input_type" not in first  # OpenAI does not take it

    async def test_cohere_gets_document_and_query_input_types(self) -> None:
        provider = LiteLLMEmbeddingProvider(_settings(embedding_model="cohere/embed-english-v3.0"))
        mock = AsyncMock(side_effect=[_response(1), _response(1)])
        with patch("litellm.aembedding", mock):
            await provider.embed_texts(["doc"])
            await provider.embed_query("q")

        assert mock.await_args_list[0].kwargs["input_type"] == "search_document"
        assert mock.await_args_list[1].kwargs["input_type"] == "search_query"

    async def test_dimension_mismatch_is_an_error(self) -> None:
        provider = LiteLLMEmbeddingProvider(_settings(embedding_dimensions=4))
        with (
            patch("litellm.aembedding", AsyncMock(return_value=_response(1, dims=3))),
            pytest.raises(ValueError, match="RAG_EMBEDDING_DIMENSIONS"),
        ):
            await provider.embed_query("q")

    async def test_api_base_is_forwarded_for_self_hosted(self) -> None:
        provider = LiteLLMEmbeddingProvider(
            _settings(
                embedding_model="ollama/nomic-embed-text",
                embedding_api_key=SecretStr(""),
                embedding_api_base="http://127.0.0.1:11434",
            )
        )
        mock = AsyncMock(return_value=_response(1))
        with patch("litellm.aembedding", mock):
            await provider.embed_query("q")

        kwargs = mock.await_args.kwargs
        assert kwargs["api_base"] == "http://127.0.0.1:11434"
        assert "api_key" not in kwargs

    async def test_health_check_false_on_failure(self) -> None:
        provider = LiteLLMEmbeddingProvider(_settings())
        with patch("litellm.aembedding", AsyncMock(side_effect=RuntimeError("401"))):
            assert await provider.health_check() is False


class TestFactoryAndResolver:
    def test_factory_builds_litellm_provider(self) -> None:
        assert isinstance(_build_provider("litellm", _settings()), LiteLLMEmbeddingProvider)

    def test_catalog_model_resolves(self) -> None:
        resolved = ConfigResolver(_settings()).resolve_embedding(None)
        assert resolved.provider == "litellm"
        assert resolved.dimensions == 1536  # from the catalog, not the setting

    def test_operator_default_outside_catalog_is_trusted(self) -> None:
        settings = _settings(embedding_model="voyage/voyage-3-large", embedding_dimensions=2048)
        resolved = ConfigResolver(settings).resolve_embedding(None)
        assert (resolved.model, resolved.dimensions) == ("voyage/voyage-3-large", 2048)

    def test_corpus_override_outside_catalog_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="No catalog entry"):
            ConfigResolver(_settings()).resolve_embedding(
                {"provider": "litellm", "model": "someone/unknown-model"}
            )
