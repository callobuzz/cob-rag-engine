"""The engine owns `system:` documents (the assistant's business card): the assistant's
settings change them, a document or ingest route never does."""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.pipeline.records import is_system_record_id

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def refuse_system_document(*documents: Document) -> None:
    if any(is_system_record_id(doc.external_id) for doc in documents):
        raise APIError(
            409,
            "This document is managed by the engine (the assistant's business card). "
            "Change the business card in the assistant's settings instead.",
            "system_document",
        )


async def refuse_system_target(
    session: "AsyncSession", corpus_id: UUID, document_id: UUID | None
) -> None:
    """Refuse a replace / new-version upload aimed at a system document of this corpus."""
    if document_id is None:
        return
    external_id = (
        await session.execute(
            select(Document.external_id).where(
                Document.id == document_id, Document.corpus_id == corpus_id
            )
        )
    ).scalar_one_or_none()
    if is_system_record_id(external_id):
        refuse_system_document(Document(external_id=external_id))
