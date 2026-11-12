"""Model-specific query / passage instructions for embedding models."""

from types import SimpleNamespace
from typing import Any

import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.embeddings.prompts import default_prefixes, resolve_prefixes

BGE_EN = "Represent this sentence for searching relevant passages: "


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("BAAI/bge-base-en-v1.5", (BGE_EN, "")),
        ("BAAI/bge-large-en", (BGE_EN, "")),
        ("mixedbread-ai/mxbai-embed-large-v1", (BGE_EN, "")),
        ("intfloat/e5-base-v2", ("query: ", "passage: ")),
        ("intfloat/multilingual-e5-large", ("query: ", "passage: ")),
        ("nomic-ai/nomic-embed-text-v1.5", ("search_query: ", "search_document: ")),
        ("BAAI/bge-m3", ("", "")),
        ("openai/text-embedding-3-small", ("", "")),
        ("sentence-transformers/all-MiniLM-L6-v2", ("", "")),
    ],
)
def test_known_conventions(model: str, expected: tuple[str, str]) -> None:
    assert default_prefixes(model) == expected


def test_overrides_win_and_empty_string_disables() -> None:
    assert resolve_prefixes("BAAI/bge-base-en-v1.5", "", None) == ("", "")
    assert resolve_prefixes("BAAI/bge-m3", "q: ", "p: ") == ("q: ", "p: ")


def _settings(**overrides: Any) -> Settings:
    return Settings(embedding_model="intfloat/e5-base-v2", embedding_dimensions=768, **overrides)


async def test_tei_prefixes_queries_and_passages() -> None:
    from callobuzz_rag.embeddings.tei import TEIEmbeddingProvider

    provider = TEIEmbeddingProvider(_settings())
    sent: list[list[str]] = []

    async def fake_batch(texts: list[str]) -> list[list[float]]:
        sent.append(texts)
        return [[0.0] * 768 for _ in texts]

    provider._embed_batch = fake_batch  # type: ignore[method-assign]
    await provider.embed_texts(["doc"])
    await provider.embed_query("question")
    assert sent == [["passage: doc"], ["query: question"]]
    await provider.close()


async def test_local_provider_prefixes_queries_and_passages() -> None:
    from callobuzz_rag.embeddings.bge_m3 import BgeM3EmbeddingProvider

    provider = BgeM3EmbeddingProvider(_settings())
    sent: list[list[str]] = []

    def fake_encode(texts: list[str], prompt_name: str | None = None) -> list[list[float]]:
        sent.append(texts)
        return [[0.0] * 768 for _ in texts]

    provider._encode_sync = fake_encode  # type: ignore[method-assign]
    await provider.embed_texts(["doc"])
    await provider.embed_query("question")
    assert sent == [["passage: doc"], ["query: question"]]


async def test_litellm_provider_prefixes_queries() -> None:
    from unittest.mock import patch

    from callobuzz_rag.embeddings.litellm_embed import LiteLLMEmbeddingProvider

    provider = LiteLLMEmbeddingProvider(
        Settings(embedding_model="ollama/nomic-embed-text", embedding_dimensions=2)
    )
    captured: dict[str, Any] = {}

    async def fake_aembedding(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(data=[{"embedding": [0.1, 0.2], "index": 0}], model="m")

    with patch("litellm.aembedding", side_effect=fake_aembedding):
        await provider.embed_query("question")
    assert captured["input"] == ["search_query: question"]


async def test_bge_m3_takes_no_prefix_on_tei_and_local() -> None:
    from callobuzz_rag.embeddings.bge_m3 import BgeM3EmbeddingProvider
    from callobuzz_rag.embeddings.tei import TEIEmbeddingProvider

    settings = Settings(embedding_model="BAAI/bge-m3", embedding_dimensions=1024)
    tei = TEIEmbeddingProvider(settings)
    sent: list[list[str]] = []

    async def fake_batch(texts: list[str]) -> list[list[float]]:
        sent.append(texts)
        return [[0.0] * 1024 for _ in texts]

    tei._embed_batch = fake_batch  # type: ignore[method-assign]
    await tei.embed_texts(["दस्तावेज़"])
    await tei.embed_query("question")
    assert sent == [["दस्तावेज़"], ["question"]]
    await tei.close()

    local = BgeM3EmbeddingProvider(settings)
    sent_local: list[list[str]] = []

    def fake_encode(texts: list[str], prompt_name: str | None = None) -> list[list[float]]:
        sent_local.append(texts)
        return [[0.0] * 1024 for _ in texts]

    local._encode_sync = fake_encode  # type: ignore[method-assign]
    await local.embed_texts(["doc"])
    await local.embed_query("question")
    assert sent_local == [["doc"], ["question"]]
