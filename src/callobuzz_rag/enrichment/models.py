"""Enrichment output models."""

from dataclasses import dataclass, field


@dataclass
class EnrichmentResult:
    """Metadata enrichment output for a single chunk.

    Maps to the chunk_metadata database table.
    """

    summary: str | None = None
    keywords: list[str] = field(default_factory=list)
    language: str | None = None
    confidence_score: float = 0.0
    hypothetical_questions: list[str] = field(default_factory=list)  # Empty in Phase 1
    tags: list[str] = field(default_factory=list)  # Empty in Phase 1
