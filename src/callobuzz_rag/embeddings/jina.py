"""Jina Embeddings v3 provider implementation.

Uses httpx async client to call the Jina API.
Supports batching, task types, and Matryoshka dimensions.
"""

import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from callobuzz_rag.core.config import Settings
from callobuzz_rag.embeddings.base import EmbeddingProvider, EmbeddingResult

logger = logging.getLogger(__name__)

JINA_API_URL = "https://api.jina.ai/v1/embeddings"


class JinaEmbeddingProvider(EmbeddingProvider):
    """Jina Embeddings v3 API provider."""

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.jina_api_key.get_secret_value()
        self._model = settings.jina_model
        self._dimensions = settings.jina_dimensions
        self._batch_size = settings.jina_batch_size
        self._client = httpx.AsyncClient(
            timeout=60.0,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )

    async def embed_texts(
        self, texts: list[str], task: str = "retrieval.passage"
    ) -> list[EmbeddingResult]:
        """Embed multiple texts with automatic batching."""
        all_results: list[EmbeddingResult] = []

        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            batch_results = await self._embed_batch(batch, task)
            all_results.extend(batch_results)

        return all_results

    async def embed_query(self, text: str) -> EmbeddingResult:
        """Embed a single query using retrieval.query task type."""
        results = await self._embed_batch([text], task="retrieval.query")
        return results[0]

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError)),
    )
    async def _embed_batch(self, texts: list[str], task: str) -> list[EmbeddingResult]:
        """Make a single API call for a batch of texts."""
        payload: dict[str, Any] = {
            "model": self._model,
            "input": texts,
            "task": task,
            "dimensions": self._dimensions,
        }

        response = await self._client.post(JINA_API_URL, json=payload)
        response.raise_for_status()
        data: dict[str, Any] = response.json()

        usage = data.get("usage", {})
        total_tokens: int | None = usage.get("total_tokens")

        # Sort by index to maintain input order
        items: list[dict[str, Any]] = sorted(data["data"], key=lambda x: x.get("index", 0))

        results: list[EmbeddingResult] = []
        for item in items:
            vector: list[float] = item["embedding"]

            if len(vector) != self._dimensions:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {self._dimensions}, "
                    f"got {len(vector)}. Check RAG_JINA_DIMENSIONS setting."
                )

            results.append(
                EmbeddingResult(
                    vector=vector,
                    model=self._model,
                    model_version=data.get("model", self._model),
                    dimensions=self._dimensions,
                    token_count=total_tokens,
                )
            )

        return results

    async def health_check(self) -> bool:
        """Check if Jina API is reachable by embedding a test string."""
        try:
            await self._embed_batch(["health check"], task="retrieval.passage")
        except Exception:
            return False
        return True

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
