"""BGE-M3 local embedding provider via sentence-transformers.

Loads BAAI/bge-m3 (or configured model) lazily on first call.
Inference runs in a thread pool via asyncio.to_thread() to avoid
blocking the event loop — same pattern as BGERerankProvider.
"""

import asyncio
import logging
import threading
from typing import Any

from callobuzz_rag.core.config import Settings
from callobuzz_rag.embeddings.base import EmbeddingProvider, EmbeddingResult

logger = logging.getLogger(__name__)

# One loaded model per (name, dimensions) per process. The worker builds a fresh
# provider for every Celery task; without this each task reloaded the weights,
# which on a GPU took ~12x longer than embedding the task's 1000 chunks.
_MODELS: dict[tuple[str, int, bool], Any] = {}
_MODELS_LOCK = threading.Lock()


class BgeM3EmbeddingProvider(EmbeddingProvider):
    """In-process BGE-M3 embedding provider using sentence-transformers.

    The model is loaded lazily on the first embed call to avoid
    blocking application startup. Subsequent calls reuse the model.
    """

    def __init__(self, settings: Settings) -> None:
        self._model_name = settings.embedding_model
        self._dimensions = settings.embedding_dimensions
        self._batch_size = max(1, settings.embedding_batch_size)
        self._fp16 = settings.embedding_fp16
        self._model: Any = None
        self._set_prefixes(settings)

    def _ensure_model(self) -> None:
        """Load the sentence-transformers model once per process, then reuse it."""
        if self._model is not None:
            return
        key = (self._model_name, self._dimensions, self._fp16)
        with _MODELS_LOCK:
            if key not in _MODELS:
                from sentence_transformers import SentenceTransformer

                logger.info(
                    "Loading embedding model: %s (this may take a moment)",
                    self._model_name,
                )
                try:
                    model = SentenceTransformer(
                        self._model_name,
                        truncate_dim=self._dimensions,
                    )
                    # Half precision on a GPU only; on CPU it is slower, not faster.
                    if self._fp16 and str(model.device).startswith("cuda"):
                        model.half()
                    _MODELS[key] = model
                except Exception as e:
                    raise RuntimeError(
                        f"Failed to load embedding model '{self._model_name}': {e}"
                    ) from e
                logger.info("Embedding model loaded successfully")
            self._model = _MODELS[key]

    def _encode_sync(self, texts: list[str], prompt_name: str | None = None) -> list[list[float]]:
        """Synchronous encoding — runs in a thread."""
        self._ensure_model()
        assert self._model is not None

        embeddings = self._model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=self._batch_size,
            show_progress_bar=False,
            prompt_name=prompt_name,
        )

        results: list[list[float]] = []
        for emb in embeddings:
            vector = emb.tolist() if hasattr(emb, "tolist") else list(emb)
            if len(vector) != self._dimensions:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {self._dimensions}, "
                    f"got {len(vector)}. Check RAG_EMBEDDING_DIMENSIONS setting."
                )
            results.append(vector)

        return results

    async def embed_texts(
        self, texts: list[str], task: str = "retrieval.passage"
    ) -> list[EmbeddingResult]:
        """Embed multiple texts using the local model."""
        if not texts:
            return []

        vectors = await asyncio.to_thread(self._encode_sync, self._prefixed(texts, task))

        return [
            EmbeddingResult(
                vector=vector,
                model=self._model_name,
                model_version=self._model_name,
                dimensions=self._dimensions,
                token_count=None,
            )
            for vector in vectors
        ]

    async def embed_query(self, text: str) -> EmbeddingResult:
        """Embed a single query using the local model."""
        query = self._prefixed([text], "retrieval.query")
        vectors = await asyncio.to_thread(self._encode_sync, query)
        return EmbeddingResult(
            vector=vectors[0],
            model=self._model_name,
            model_version=self._model_name,
            dimensions=self._dimensions,
            token_count=None,
        )

    async def health_check(self) -> bool:
        """Check if the provider is configured and operational."""
        try:
            if self._model is not None:
                await asyncio.to_thread(self._encode_sync, ["health check"])
            return True
        except Exception:
            return False

    async def close(self) -> None:
        """Drop this provider's handle. The weights stay loaded for the next provider
        in this process; they are freed when the process exits."""
        self._model = None
