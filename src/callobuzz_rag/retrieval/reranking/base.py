"""Abstract base class for reranking providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RerankResult:
    """A single reranked result.

    Attributes:
        index: Original index in the input document list.
        score: Cross-encoder relevance score (higher = more relevant).
        text: The document text that was scored.
    """

    index: int
    score: float
    text: str


class RerankProvider(ABC):
    """Interface for cross-encoder reranking providers.

    Implementations must be safe to call from async contexts.
    CPU-bound inference should use asyncio.to_thread().
    """

    @abstractmethod
    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int = 10,
    ) -> list[RerankResult]:
        """Rerank documents by relevance to the query.

        Args:
            query: The user's search query.
            documents: List of document texts to rerank.
            top_k: Number of top results to return.

        Returns:
            Top-k results sorted by cross-encoder score (descending).

        Raises:
            RerankError: If reranking fails.
        """
