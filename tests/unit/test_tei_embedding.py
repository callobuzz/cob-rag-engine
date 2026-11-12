"""Unit tests for TEI HTTP embedding provider."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from callobuzz_rag.embeddings.base import EmbeddingProvider, EmbeddingResult
from callobuzz_rag.embeddings.tei import TEIEmbeddingProvider


class TestTEIEmbeddingProvider:
    def _make_settings(self) -> MagicMock:
        settings = MagicMock()
        settings.tei_url = "http://localhost:8081"
        settings.embedding_model = "BAAI/bge-m3"
        settings.embedding_dimensions = 1024
        settings.tei_batch_size = 32
        settings.tei_concurrency = 4
        settings.embedding_query_prefix = None
        settings.embedding_passage_prefix = None
        return settings

    @pytest.mark.asyncio
    async def test_concurrent_sub_batches_keep_their_order(self) -> None:
        """Sub-batches run in parallel; a slow first batch must not reorder results."""
        import asyncio

        settings = self._make_settings()
        settings.tei_batch_size = 2
        provider = TEIEmbeddingProvider(settings)

        async def _fake_post(url: str, json: dict) -> MagicMock:
            first = json["inputs"][0]
            await asyncio.sleep(0.05 if first.endswith("0") else 0)  # batch 0 finishes last
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = [[float(t.split()[-1])] + [0.0] * 1023 for t in json["inputs"]]
            return resp

        texts = [f"chunk {i}" for i in range(6)]
        with patch.object(provider._client, "post", new_callable=AsyncMock, side_effect=_fake_post):
            results = await provider.embed_texts(texts)
        assert [r.vector[0] for r in results] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]

    def test_implements_abc(self) -> None:
        settings = self._make_settings()
        provider = TEIEmbeddingProvider(settings)
        assert isinstance(provider, EmbeddingProvider)

    @pytest.mark.asyncio
    async def test_embed_texts_returns_results(self) -> None:
        settings = self._make_settings()
        provider = TEIEmbeddingProvider(settings)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [
            [0.1] * 1024,
            [0.2] * 1024,
        ]

        with patch.object(provider._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            results = await provider.embed_texts(["hello world", "test text"])

        assert len(results) == 2
        assert all(isinstance(r, EmbeddingResult) for r in results)
        assert results[0].dimensions == 1024
        assert results[0].model == "BAAI/bge-m3"
        assert len(results[0].vector) == 1024

    @pytest.mark.asyncio
    async def test_embed_query_returns_single_result(self) -> None:
        settings = self._make_settings()
        provider = TEIEmbeddingProvider(settings)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [[0.1] * 1024]

        with patch.object(provider._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            result = await provider.embed_query("what is RAG?")

        assert isinstance(result, EmbeddingResult)
        assert result.dimensions == 1024

    @pytest.mark.asyncio
    async def test_embed_texts_splits_into_batches(self) -> None:
        """Inputs beyond tei_batch_size are sent in multiple requests.

        Regression test: TEI rejects a single /embed request whose input
        list exceeds its server-side max_client_batch_size (default 32) with
        HTTP 422. Documents that chunk into >32 pieces must be split, else the
        whole document fails to embed.
        """
        settings = self._make_settings()
        settings.tei_batch_size = 3
        provider = TEIEmbeddingProvider(settings)

        # Each POST echoes one vector per input in that sub-batch.
        def _make_resp(input_count: int) -> MagicMock:
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = [[0.1] * 1024 for _ in range(input_count)]
            return resp

        async def _fake_post(url: str, json: dict) -> MagicMock:
            return _make_resp(len(json["inputs"]))

        texts = [f"chunk {i}" for i in range(7)]  # 7 > batch_size 3 -> 3 calls
        with patch.object(
            provider._client, "post", new_callable=AsyncMock, side_effect=_fake_post
        ) as mock_post:
            results = await provider.embed_texts(texts)

        assert len(results) == 7  # 1:1 mapping preserved across sub-batches
        assert mock_post.call_count == 3  # ceil(7/3)
        sent_sizes = [len(c.kwargs["json"]["inputs"]) for c in mock_post.call_args_list]
        assert sent_sizes == [3, 3, 1]
        assert all(len(r.vector) == 1024 for r in results)

    @pytest.mark.asyncio
    async def test_embed_texts_empty_list(self) -> None:
        settings = self._make_settings()
        provider = TEIEmbeddingProvider(settings)
        results = await provider.embed_texts([])
        assert results == []

    @pytest.mark.asyncio
    async def test_dimension_mismatch_raises(self) -> None:
        settings = self._make_settings()
        settings.embedding_dimensions = 1024
        provider = TEIEmbeddingProvider(settings)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [[0.1] * 512]

        with patch.object(provider._client, "post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            with pytest.raises(ValueError, match="dimension mismatch"):
                await provider.embed_texts(["hello"])

    @pytest.mark.asyncio
    async def test_health_check_success(self) -> None:
        settings = self._make_settings()
        provider = TEIEmbeddingProvider(settings)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch.object(provider._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            result = await provider.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self) -> None:
        settings = self._make_settings()
        provider = TEIEmbeddingProvider(settings)

        with patch.object(provider._client, "get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.ConnectError("Connection refused")
            result = await provider.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_close_closes_client(self) -> None:
        settings = self._make_settings()
        provider = TEIEmbeddingProvider(settings)

        with patch.object(provider._client, "aclose", new_callable=AsyncMock) as mock_close:
            await provider.close()
            mock_close.assert_called_once()
