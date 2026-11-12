"""Abstract base class for embedding providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings


@dataclass
class EmbeddingResult:
    """Result from embedding a single text."""

    vector: list[float]
    model: str
    model_version: str
    dimensions: int
    token_count: int | None = None


class EmbeddingProvider(ABC):
    """Interface for text embedding."""

    # Instructions the model expects in front of queries / passages; see
    # embeddings/prompts.py. Providers that embed an open-weight model set these.
    query_prefix: str = ""
    passage_prefix: str = ""

    @property
    def model_id(self) -> str | None:
        """The model this provider embeds with; keys the query-embedding cache."""
        for attr in ("_model_name", "_model"):
            value = getattr(self, attr, None)
            if isinstance(value, str) and value:
                return value
        return None

    def _set_prefixes(self, settings: "Settings") -> None:
        from callobuzz_rag.embeddings.prompts import resolve_prefixes

        self.query_prefix, self.passage_prefix = resolve_prefixes(
            settings.embedding_model,
            settings.embedding_query_prefix,
            settings.embedding_passage_prefix,
        )

    def _prefixed(self, texts: list[str], task: str) -> list[str]:
        prefix = self.query_prefix if task == "retrieval.query" else self.passage_prefix
        return [prefix + t for t in texts] if prefix else texts

    @abstractmethod
    async def embed_texts(
        self, texts: list[str], task: str = "retrieval.passage"
    ) -> list[EmbeddingResult]:
        """Embed multiple texts. Used for document chunks."""

    @abstractmethod
    async def embed_query(self, text: str) -> EmbeddingResult:
        """Embed a single query. Uses query-optimized task type."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the embedding service is reachable."""

    async def close(self) -> None:
        """Release any held resources (HTTP clients, model handles).

        Concrete, not abstract: every current provider owns something to close,
        and callers such as the Celery beat tasks close the provider in a
        `finally` block. Declaring it here makes that call part of the contract
        rather than duck typing that happens to work — a new provider with
        nothing to release inherits this no-op instead of crashing the caller.
        """
        return None
