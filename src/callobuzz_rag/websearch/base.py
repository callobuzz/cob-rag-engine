"""Abstract base class for web search providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WebSearchResult:
    """A single web search result."""

    title: str
    url: str
    snippet: str
    score: float = 0.0


class WebSearchProvider(ABC):
    """Interface for external web search APIs."""

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> list[WebSearchResult]:
        """Search the web and return results."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the search API is reachable."""
