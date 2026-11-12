"""Unit tests for BGE-M3 local embedding provider."""

from unittest.mock import MagicMock

import pytest

from callobuzz_rag.embeddings.base import EmbeddingProvider, EmbeddingResult
from callobuzz_rag.embeddings.bge_m3 import BgeM3EmbeddingProvider


class TestBgeM3EmbeddingProvider:
    def _make_settings(self) -> MagicMock:
        settings = MagicMock()
        settings.embedding_model = "BAAI/bge-m3"
        settings.embedding_dimensions = 1024
        settings.embedding_batch_size = 64
        settings.embedding_query_prefix = None
        settings.embedding_passage_prefix = None
        settings.embedding_fp16 = True
        return settings

    def test_weights_load_once_per_process(self) -> None:
        """Every Celery task builds a new provider; they must share one loaded model."""
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch

        from callobuzz_rag.embeddings import bge_m3

        bge_m3._MODELS.clear()
        loader = MagicMock()
        # A stand-in module: patching the real one would import torch (~50s).
        fake = SimpleNamespace(SentenceTransformer=loader)
        with patch.dict(sys.modules, {"sentence_transformers": fake}):
            first = BgeM3EmbeddingProvider(self._make_settings())
            second = BgeM3EmbeddingProvider(self._make_settings())
            first._ensure_model()
            second._ensure_model()
        assert loader.call_count == 1
        assert first._model is second._model
        bge_m3._MODELS.clear()

    def test_half_precision_on_gpu_only(self) -> None:
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch

        from callobuzz_rag.embeddings import bge_m3

        for device, halved in (("cuda:0", True), ("cpu", False)):
            bge_m3._MODELS.clear()
            model = MagicMock(device=device)
            fake = SimpleNamespace(SentenceTransformer=MagicMock(return_value=model))
            with patch.dict(sys.modules, {"sentence_transformers": fake}):
                BgeM3EmbeddingProvider(self._make_settings())._ensure_model()
            assert model.half.called is halved
        bge_m3._MODELS.clear()

    def test_implements_abc(self) -> None:
        settings = self._make_settings()
        provider = BgeM3EmbeddingProvider(settings)
        assert isinstance(provider, EmbeddingProvider)

    def test_model_not_loaded_on_init(self) -> None:
        settings = self._make_settings()
        provider = BgeM3EmbeddingProvider(settings)
        assert provider._model is None

    @pytest.mark.asyncio
    async def test_embed_texts_returns_results(self) -> None:
        settings = self._make_settings()
        provider = BgeM3EmbeddingProvider(settings)

        mock_model = MagicMock()
        mock_model.encode.return_value = [
            [0.1] * 1024,
            [0.2] * 1024,
        ]
        provider._model = mock_model

        results = await provider.embed_texts(["hello world", "test text"])
        assert len(results) == 2
        assert all(isinstance(r, EmbeddingResult) for r in results)
        assert results[0].dimensions == 1024
        assert results[0].model == "BAAI/bge-m3"
        assert len(results[0].vector) == 1024

    @pytest.mark.asyncio
    async def test_embed_query_returns_single_result(self) -> None:
        settings = self._make_settings()
        provider = BgeM3EmbeddingProvider(settings)

        mock_model = MagicMock()
        mock_model.encode.return_value = [[0.1] * 1024]
        provider._model = mock_model

        result = await provider.embed_query("what is RAG?")
        assert isinstance(result, EmbeddingResult)
        assert result.dimensions == 1024
        assert result.model == "BAAI/bge-m3"

    @pytest.mark.asyncio
    async def test_embed_texts_empty_list(self) -> None:
        settings = self._make_settings()
        provider = BgeM3EmbeddingProvider(settings)
        results = await provider.embed_texts([])
        assert results == []

    @pytest.mark.asyncio
    async def test_dimension_mismatch_raises(self) -> None:
        settings = self._make_settings()
        settings.embedding_dimensions = 1024
        provider = BgeM3EmbeddingProvider(settings)

        mock_model = MagicMock()
        mock_model.encode.return_value = [[0.1] * 512]
        provider._model = mock_model

        with pytest.raises(ValueError, match="dimension mismatch"):
            await provider.embed_texts(["hello"])

    @pytest.mark.asyncio
    async def test_health_check_with_model_loaded(self) -> None:
        settings = self._make_settings()
        provider = BgeM3EmbeddingProvider(settings)

        mock_model = MagicMock()
        mock_model.encode.return_value = [[0.1] * 1024]
        provider._model = mock_model

        result = await provider.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_without_model(self) -> None:
        settings = self._make_settings()
        provider = BgeM3EmbeddingProvider(settings)
        result = await provider.health_check()
        assert result is True
