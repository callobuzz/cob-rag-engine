"""Hosted embeddings through LiteLLM — one provider for every paid embedding API.

`RAG_EMBEDDING_PROVIDER=litellm` with a LiteLLM model string reaches OpenAI
(`openai/text-embedding-3-small`), Cohere (`cohere/embed-english-v3.0`), Voyage
(`voyage/voyage-3`), Mistral (`mistral/mistral-embed`), Gemini
(`gemini/text-embedding-004`), Azure, Bedrock or a self-hosted Ollama
(`ollama/nomic-embed-text` + RAG_EMBEDDING_API_BASE) with no code change.
"""

import logging
from typing import Any

from callobuzz_rag.core.config import Settings
from callobuzz_rag.embeddings.base import EmbeddingProvider, EmbeddingResult

logger = logging.getLogger(__name__)

# Providers whose embedding APIs distinguish documents from queries. LiteLLM
# forwards `input_type` to them; other providers would reject the parameter.
_INPUT_TYPE_PROVIDERS = {
    "cohere": ("search_document", "search_query"),
    "voyage": ("document", "query"),
}


class LiteLLMEmbeddingProvider(EmbeddingProvider):
    """Any LiteLLM-supported embedding model, configured entirely from settings."""

    def __init__(self, settings: Settings) -> None:
        self._model = settings.embedding_model
        self._dimensions = settings.embedding_dimensions
        self._api_key = settings.embedding_api_key.get_secret_value() or None
        self._api_base = settings.embedding_api_base or None
        self._batch_size = max(1, settings.embedding_batch_size)
        self._input_types = _INPUT_TYPE_PROVIDERS.get(self._model.split("/", 1)[0])
        self._set_prefixes(settings)

    async def embed_texts(
        self, texts: list[str], task: str = "retrieval.passage"
    ) -> list[EmbeddingResult]:
        """Embed document chunks in batches of RAG_EMBEDDING_BATCH_SIZE."""
        results: list[EmbeddingResult] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            results.extend(await self._embed_batch(batch, query=task == "retrieval.query"))
        return results

    async def embed_query(self, text: str) -> EmbeddingResult:
        """Embed a single query."""
        return (await self._embed_batch([text], query=True))[0]

    async def _embed_batch(self, texts: list[str], *, query: bool) -> list[EmbeddingResult]:
        import litellm

        texts = self._prefixed(texts, "retrieval.query" if query else "retrieval.passage")
        kwargs: dict[str, Any] = {
            "model": self._model,
            "input": texts,
            # Matryoshka models (OpenAI text-embedding-3, ...) honour this; for
            # the rest LiteLLM drops it rather than failing the request.
            "dimensions": self._dimensions,
            "drop_params": True,
            "num_retries": 2,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._api_base:
            kwargs["api_base"] = self._api_base
        if self._input_types:
            kwargs["input_type"] = self._input_types[1 if query else 0]

        response = await litellm.aembedding(**kwargs)

        usage = getattr(response, "usage", None)
        token_count: int | None = getattr(usage, "prompt_tokens", None) if usage else None

        items = sorted(response.data, key=lambda item: _field(item, "index", 0))
        results: list[EmbeddingResult] = []
        for item in items:
            vector: list[float] = list(_field(item, "embedding"))
            if len(vector) != self._dimensions:
                raise ValueError(
                    f"Embedding dimension mismatch for {self._model}: expected "
                    f"{self._dimensions}, got {len(vector)}. Set RAG_EMBEDDING_DIMENSIONS "
                    "to the model's output size."
                )
            results.append(
                EmbeddingResult(
                    vector=vector,
                    model=self._model,
                    model_version=str(getattr(response, "model", None) or self._model),
                    dimensions=self._dimensions,
                    token_count=token_count,
                )
            )
        if len(results) != len(texts):
            raise ValueError(
                f"{self._model} returned {len(results)} embeddings for {len(texts)} inputs"
            )
        return results

    async def health_check(self) -> bool:
        """Reachable and authorised: embed one short string."""
        try:
            await self._embed_batch(["health check"], query=False)
        except Exception:
            logger.warning("Embedding health check failed for %s", self._model, exc_info=True)
            return False
        return True


def _field(item: Any, name: str, default: Any = None) -> Any:
    """LiteLLM returns dicts from some providers and objects from others."""
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)
