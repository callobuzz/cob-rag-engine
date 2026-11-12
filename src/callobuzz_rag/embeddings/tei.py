"""HuggingFace Text Embeddings Inference (TEI) HTTP provider.

Connects to a TEI container via HTTP. Uses httpx async client
(same pattern as JinaEmbeddingProvider).
"""

import asyncio
import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from callobuzz_rag.core.config import Settings
from callobuzz_rag.embeddings.base import EmbeddingProvider, EmbeddingResult

logger = logging.getLogger(__name__)


class TEIEmbeddingProvider(EmbeddingProvider):
    """TEI HTTP embedding provider.

    Calls the /embed endpoint of a HuggingFace Text Embeddings
    Inference container.
    """

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.tei_url.rstrip("/")
        self._model_name = settings.embedding_model
        self._dimensions = settings.embedding_dimensions
        # TEI rejects requests whose input list exceeds its server-side
        # max_client_batch_size (default 32) with HTTP 422. Documents that
        # chunk into more than this many pieces must be split across multiple
        # requests, otherwise the whole document fails to embed.
        self._batch_size = max(1, settings.tei_batch_size)
        self._concurrency = max(1, settings.tei_concurrency)
        self._set_prefixes(settings)
        self._client = httpx.AsyncClient(
            timeout=120.0,
            headers={"Content-Type": "application/json"},
        )

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
    )
    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Make a single API call to TEI /embed endpoint."""
        payload: dict[str, Any] = {
            "inputs": texts,
            "truncate": True,
        }
        response = await self._client.post(
            f"{self._base_url}/embed",
            json=payload,
        )
        response.raise_for_status()
        embeddings: list[list[float]] = response.json()
        return embeddings

    async def embed_texts(
        self, texts: list[str], task: str = "retrieval.passage"
    ) -> list[EmbeddingResult]:
        """Embed multiple texts via TEI container."""
        if not texts:
            return []

        # Split into sub-batches within TEI's max_client_batch_size cap.
        # Order is preserved so callers can zip results back to input chunks.
        # Up to `tei_concurrency` sub-batches in flight: TEI batches concurrent
        # requests together on its side, so sending them one after another left it
        # idle between round trips. gather() keeps the order.
        texts = self._prefixed(texts, task)
        batches = [
            texts[start : start + self._batch_size]
            for start in range(0, len(texts), self._batch_size)
        ]
        gate = asyncio.Semaphore(self._concurrency)

        async def one(batch: list[str]) -> list[list[float]]:
            async with gate:
                return await self._embed_batch(batch)

        vectors = [v for part in await asyncio.gather(*(one(b) for b in batches)) for v in part]

        results: list[EmbeddingResult] = []
        for vector in vectors:
            if len(vector) > self._dimensions:
                vector = vector[: self._dimensions]
            if len(vector) != self._dimensions:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {self._dimensions}, "
                    f"got {len(vector)}. Check RAG_EMBEDDING_DIMENSIONS and TEI model config."
                )
            results.append(
                EmbeddingResult(
                    vector=vector,
                    model=self._model_name,
                    model_version=self._model_name,
                    dimensions=self._dimensions,
                    token_count=None,
                )
            )
        return results

    async def embed_query(self, text: str) -> EmbeddingResult:
        """Embed a single query via TEI container."""
        results = await self.embed_texts([text], task="retrieval.query")
        return results[0]

    async def health_check(self) -> bool:
        """Check if TEI container is reachable via /health endpoint."""
        try:
            response = await self._client.get(f"{self._base_url}/health")
            response.raise_for_status()
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
