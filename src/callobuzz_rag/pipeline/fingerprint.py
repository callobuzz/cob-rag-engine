"""Fingerprint and document matching utilities."""

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import selectinload

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.db.models.document import Document


def compute_fingerprint(data: bytes) -> str:
    """Compute SHA256 fingerprint of file content."""
    return hashlib.sha256(data).hexdigest()


async def find_matching_document(
    session: AsyncSession,
    corpus_id: uuid.UUID,
    file_name: str,
) -> Document | None:
    """Find an existing document with the same filename in the same corpus.

    Returns the Document if found, None otherwise.
    Used by auto mode for filename-based version matching.
    """
    from callobuzz_rag.db.models.document import Document

    result = await session.execute(
        select(Document)
        .where(
            Document.corpus_id == corpus_id,
            Document.file_name == file_name,
        )
        .options(selectinload(Document.versions))
    )
    return result.scalar_one_or_none()
