"""Utility to build consistent S3 keys for document storage.

Path convention: corpus/{corpus_id}/raw/{document_id}
"""

import uuid


def build_raw_document_key(corpus_id: uuid.UUID, document_id: uuid.UUID) -> str:
    """Build S3 key for a raw document file."""
    return f"corpus/{corpus_id}/raw/{document_id}"


def build_processed_key(corpus_id: uuid.UUID, document_id: uuid.UUID, suffix: str) -> str:
    """Build S3 key for a processed artifact (e.g., parsed output)."""
    return f"corpus/{corpus_id}/processed/{document_id}/{suffix}"
