"""PIP1: the hosted reranker (`reranker: api`) and its selection. Nothing touches the network:
`litellm.arerank` is patched."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.api.routes.query import _select_reranker
from callobuzz_rag.core.exceptions import RerankError
from callobuzz_rag.retrieval.reranking.api import APIRerankProvider
from callobuzz_rag.retrieval.reranking.base import RerankProvider, RerankResult
from callobuzz_rag.retrieval.reranking.llm import FallbackRerankProvider

DOCS = ["Shree Dental opens at 9", "Root canal costs Rs 4,500", "Parking is free"]


def response(*pairs: tuple[int, float]) -> Any:
    return SimpleNamespace(
        results=[{"index": i, "relevance_score": s} for i, s in pairs], meta=None
    )


class Local(RerankProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def rerank(self, query: str, documents: list[str], top_k: int = 10) -> list[RerankResult]:
        self.calls += 1
        return [RerankResult(index=0, score=0.5, text=documents[0])]


async def test_scores_are_mapped_back_to_the_input_order() -> None:
    mock = AsyncMock(return_value=response((1, 0.91), (0, 0.4), (2, 0.1)))
    with patch("litellm.arerank", mock):
        out = await APIRerankProvider("cohere/rerank-v3.5", "key-1").rerank(
            "how much is a root canal?", DOCS, top_k=2
        )
    assert [(r.index, r.score, r.text) for r in out] == [
        (1, 0.91, DOCS[1]),
        (0, 0.4, DOCS[0]),
    ]
    mock.assert_awaited_once()
    kwargs = mock.await_args.kwargs
    assert kwargs["model"] == "cohere/rerank-v3.5" and kwargs["top_n"] == 2
    assert kwargs["api_key"] == "key-1" and kwargs["documents"] == DOCS


async def test_a_bad_index_from_the_service_is_dropped() -> None:
    with patch("litellm.arerank", AsyncMock(return_value=response((9, 0.9), (2, 0.5), (2, 0.4)))):
        out = await APIRerankProvider("m", "k").rerank("q", DOCS)
    assert [r.index for r in out] == [2]


async def test_no_usable_ranking_is_an_error() -> None:
    with (
        patch("litellm.arerank", AsyncMock(return_value=response())),
        pytest.raises(RerankError),
    ):
        await APIRerankProvider("m", "k").rerank("q", DOCS)


async def test_a_provider_error_is_a_rerank_error_that_never_leaks_the_key() -> None:
    boom = AsyncMock(side_effect=RuntimeError("401 for key sk-secret"))
    with patch("litellm.arerank", boom), pytest.raises(RerankError) as err:
        await APIRerankProvider("m", "sk-secret").rerank("q", DOCS)
    assert "sk-secret" not in str(err.value)


async def test_without_a_key_it_is_not_usable_and_calls_nothing() -> None:
    mock = AsyncMock()
    provider = APIRerankProvider("cohere/rerank-v3.5", "")
    assert provider.usable is False
    with patch("litellm.arerank", mock), pytest.raises(RerankError):
        await provider.rerank("q", DOCS)
    mock.assert_not_awaited()
    assert await APIRerankProvider("m", "k").rerank("q", []) == []


def _retrieval(provider: str = "api", model: str | None = "cohere/rerank-v3.5") -> Any:
    return SimpleNamespace(rerank_provider=provider, rerank_llm_model=model)


def _settings(key: str) -> Any:
    return SimpleNamespace(rerank_api_key=SimpleNamespace(get_secret_value=lambda: key))


def test_select_uses_the_api_reranker_behind_the_local_one() -> None:
    local = Local()
    got = _select_reranker(local, object(), _retrieval(), settings=_settings("k"))  # type: ignore[arg-type]
    assert isinstance(got, FallbackRerankProvider)


def test_select_without_a_key_uses_the_local_one_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    local = Local()
    got = _select_reranker(local, object(), _retrieval(), settings=_settings(""))  # type: ignore[arg-type]
    assert got is local
    assert "RAG_RERANK_API_KEY" in caplog.text


def test_select_with_no_settings_or_no_model_falls_back_to_local() -> None:
    local = Local()
    assert _select_reranker(local, object(), _retrieval(), settings=None) is local  # type: ignore[arg-type]
    assert (
        _select_reranker(local, object(), _retrieval(model=None), settings=_settings("k")) is local  # type: ignore[arg-type]
    )


async def test_an_api_outage_falls_back_to_the_local_reranker() -> None:
    local = Local()
    got = _select_reranker(local, object(), _retrieval(), settings=_settings("k"))  # type: ignore[arg-type]
    assert got is not None
    with patch("litellm.arerank", AsyncMock(side_effect=RuntimeError("down"))):
        out = await got.rerank("q", DOCS)
    assert local.calls == 1 and out[0].index == 0


def test_the_resolver_accepts_api_as_a_rerank_provider() -> None:
    from callobuzz_rag.core.config_resolver import ConfigResolver

    assert ConfigResolver._normalize_rerank_provider("API") == "api"
    assert ConfigResolver._normalize_rerank_provider("nonsense") == "local"
