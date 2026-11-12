"""Data models for parsed document output.

These are our INTERNAL models — decoupled from Docling's models.
Every parser provider maps its output to these.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ElementType(StrEnum):
    """Types of document elements."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    CODE = "code"
    LIST_ITEM = "list_item"
    IMAGE = "image"
    CAPTION = "caption"
    FORMULA = "formula"
    UNKNOWN = "unknown"


@dataclass
class TableData:
    """Structured table representation."""

    markdown: str  # Markdown table format
    json_rows: list[dict[str, Any]]  # List of row dicts [{col: val}, ...]
    num_rows: int
    num_cols: int


@dataclass
class ParsedElement:
    """A single document element (heading, paragraph, table, etc.)."""

    type: ElementType
    text: str  # Text content
    page: int | None = None  # Page number (1-indexed, None if unknown)
    level: int | None = None  # Heading level (1=H1, 2=H2, etc.)
    table_data: TableData | None = None  # Populated only for TABLE elements
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    """Complete parsed output from a document."""

    elements: list[ParsedElement]
    page_count: int | None = None
    source_filename: str | None = None
    mime_type: str | None = None
    parser_name: str = ""
    parser_version: str = ""
    warnings: list[str] = field(default_factory=list)
