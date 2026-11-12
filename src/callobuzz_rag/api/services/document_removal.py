"""Removing a document with its vectors (shared by the delete route and the business card)."""

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.document import Document

logger = logging.getLogger(__name__)


async def dual_write_collection(session: "AsyncSession", corpus_id: UUID) -> str | None:
    """A running embedding migration's new collection, which every change to a
    version's points has to reach too, or None. Best effort, like every dual-write."""
    from callobuzz_rag.migration.context import get_dual_write_collection

    try:
        return await get_dual_write_collection(session, corpus_id)
    except Exception:
        logger.warning("Dual-write check failed; only the corpus's collection is changed")
        return None


async def dual_delete(vector_store: Any, collection: str | None, filters: dict[str, Any]) -> None:
    if collection is None:
        return
    try:
        await vector_store.delete_by_filter(collection, filters)
    except Exception as e:
        logger.warning("Points %s left in dual-write collection %s: %s", filters, collection, e)


async def remove_document(
    session: "AsyncSession", vector_store: Any, corpus: "Corpus", doc: "Document"
) -> None:
    """Purge the document's vectors (every version, and a migration's new collection), then
    delete the row, which cascades to versions, chunks, embeddings and metadata. The caller
    commits."""
    collection = corpus.qdrant_collection or "chunks"
    await vector_store.delete_by_filter(collection, {"document_id": str(doc.id)})
    dual = await dual_write_collection(session, corpus.id)
    await dual_delete(vector_store, dual, {"document_id": str(doc.id)})
    await session.delete(doc)
