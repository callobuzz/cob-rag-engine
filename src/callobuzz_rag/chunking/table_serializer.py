"""Table serialization for RAG-optimized retrieval."""

import json

from callobuzz_rag.parsing.models import TableData


def serialize_table_for_embedding(table_data: TableData) -> str:
    """Convert table to text optimized for embedding.

    Uses markdown format — best balance of readability and embedding quality.
    """
    return table_data.markdown


def serialize_table_to_json(table_data: TableData) -> str:
    """Convert table to JSON for structured storage."""
    return json.dumps(table_data.json_rows, ensure_ascii=False)
