"""Unit tests for cross-encoder reranking (mocked — no model download)."""

from unittest.mock import MagicMock, patch

import pytest

from callobuzz_rag.core.exceptions import RerankError
from callobuzz_rag.retrieval.reranking.base import RerankProvider, RerankResult
from callobuzz_rag.retrieval.reranking.bge import BGERerankProvider


class TestRerankResult:
    def test_fields(self) -> None:
        r = RerankResult(index=0, score=0.95, text="hello world")
        assert r.index == 0
        assert r.score == 0.95
        assert r.text == "hello world"


class TestRerankProviderABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            RerankProvider()  # type: ignore[abstract]


class TestBGERerankProvider:
    def test_init_defaults(self) -> None:
        provider = BGERerankProvider()
        assert provider._model_name == "BAAI/bge-reranker-v2-m3"
        assert provider._max_length == 1024
        assert provider._model is None

    def test_init_custom(self) -> None:
        provider = BGERerankProvider(model_name="custom/model", max_length=512)
        assert provider._model_name == "custom/model"
        assert provider._max_length == 512

    @pytest.mark.asyncio
    async def test_empty_documents_returns_empty(self) -> None:
        provider = BGERerankProvider()
        results = await provider.rerank("query", [], top_k=10)
        assert results == []
        assert provider._model is None  # Model not loaded

    @pytest.mark.asyncio
    async def test_rerank_with_mock_model(self) -> None:
        provider = BGERerankProvider()

        mock_model = MagicMock()
        mock_model.predict.return_value = [0.9, 0.3, 0.7]
        provider._model = mock_model

        results = await provider.rerank(
            "test query",
            ["doc1", "doc2", "doc3"],
            top_k=2,
        )

        assert len(results) == 2
        assert results[0].score == 0.9
        assert results[0].text == "doc1"
        assert results[0].index == 0
        assert results[1].score == 0.7
        assert results[1].text == "doc3"
        assert results[1].index == 2

    @pytest.mark.asyncio
    async def test_rerank_top_k_clamped(self) -> None:
        provider = BGERerankProvider()
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.9, 0.3]
        provider._model = mock_model

        results = await provider.rerank("q", ["a", "b"], top_k=100)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_rerank_sorted_descending(self) -> None:
        provider = BGERerankProvider()
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.1, 0.9, 0.5, 0.3]
        provider._model = mock_model

        results = await provider.rerank("q", ["a", "b", "c", "d"], top_k=4)
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_rerank_preserves_original_index(self) -> None:
        provider = BGERerankProvider()
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.1, 0.9]
        provider._model = mock_model

        results = await provider.rerank("q", ["low", "high"], top_k=2)
        assert results[0].index == 1  # "high" was at index 1
        assert results[1].index == 0  # "low" was at index 0

    @pytest.mark.asyncio
    async def test_predict_failure_raises_rerank_error(self) -> None:
        provider = BGERerankProvider()
        mock_model = MagicMock()
        mock_model.predict.side_effect = RuntimeError("GPU OOM")
        provider._model = mock_model

        with pytest.raises(RerankError, match="Cross-encoder prediction failed"):
            await provider.rerank("q", ["doc"], top_k=1)

    @pytest.mark.asyncio
    async def test_single_document(self) -> None:
        provider = BGERerankProvider()
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.85]
        provider._model = mock_model

        results = await provider.rerank("q", ["only doc"], top_k=1)
        assert len(results) == 1
        assert results[0].score == 0.85
        assert results[0].text == "only doc"

    def test_ensure_model_lazy_load(self) -> None:
        provider = BGERerankProvider()
        assert provider._model is None

        mock_ce = MagicMock()
        # Directly set model to simulate lazy load
        provider._model = mock_ce
        assert provider._model is mock_ce

        # _ensure_model should be a no-op when model already loaded
        provider._ensure_model()
        assert provider._model is mock_ce

    @pytest.mark.asyncio
    async def test_model_load_failure_raises_rerank_error(self) -> None:
        """CrossEncoder constructor exception -> RerankError."""
        import sys

        # Create a mock sentence_transformers module with a failing CrossEncoder
        mock_st = MagicMock()
        mock_st.CrossEncoder = MagicMock(side_effect=Exception("model not found"))
        with patch.dict(sys.modules, {"sentence_transformers": mock_st}):
            provider = BGERerankProvider(model_name="nonexistent/model")
            with pytest.raises(RerankError, match="Failed to load"):
                await provider.rerank("query", ["doc1"])

    @pytest.mark.asyncio
    async def test_nan_scores_raise_rerank_error(self) -> None:
        """Model returning NaN scores -> RerankError."""
        provider = BGERerankProvider()
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.9, float("nan"), 0.5]
        provider._model = mock_model

        with pytest.raises(RerankError, match="non-finite scores"):
            await provider.rerank("query", ["a", "b", "c"], top_k=3)

    @pytest.mark.asyncio
    async def test_inf_scores_raise_rerank_error(self) -> None:
        """Model returning inf scores -> RerankError."""
        provider = BGERerankProvider()
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.9, float("inf")]
        provider._model = mock_model

        with pytest.raises(RerankError, match="non-finite scores"):
            await provider.rerank("query", ["a", "b"], top_k=2)
