"""Abstract base class for enrichment providers."""

from abc import ABC, abstractmethod

from callobuzz_rag.chunking.models import ChunkResult
from callobuzz_rag.enrichment.models import EnrichmentResult


class EnrichmentProvider(ABC):
    """Interface for chunk metadata enrichment."""

    @abstractmethod
    async def enrich(self, chunk: ChunkResult) -> EnrichmentResult:
        """Enrich a single chunk with metadata."""

    async def enrich_batch(self, chunks: list[ChunkResult]) -> list[EnrichmentResult]:
        """Enrich multiple chunks. Default: sequential. Override for batching."""
        return [await self.enrich(chunk) for chunk in chunks]
