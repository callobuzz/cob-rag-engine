"""Chunk data models."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChunkResult:
    """A single chunk ready for embedding and storage."""

    text: str
    chunk_index: int
    structure_path: list[str]  # Heading hierarchy: ["Title", "Section 1"]
    start_offset: int | None = None
    end_offset: int | None = None
    page: int | None = None
    length_tokens: int = 0
    is_table: bool = False
    is_code: bool = False
    parent_chunk_index: int | None = None  # Index of parent chunk (if child)
    is_parent: bool = False  # True if this is a section-level parent
    context_preamble: str | None = None  # Phase 2: contextual chunking preamble
    # The chunk's own text, before overlap from the previous chunk was prepended. Duplicate
    # detection hashes this: overlap would otherwise make the same passage differ by neighbour.
    core_text: str | None = None
    table_json: str | None = None  # JSON representation of table (if is_table)
    metadata: dict[str, Any] = field(default_factory=dict)
