"""Taking a learned page out of the answers without losing it (BIZ1; BIZ2 takes this over).

Archiving deactivates the document's active version and removes its vectors (also from a running
embedding migration's new collection), in the order the merge route uses. The document row, its
versions and its history stay, so the owner can restore the page from the document history. The
caller sets the page's own status and commits.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from callobuzz_rag.api.services.document_removal import dual_delete, dual_write_collection
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.db.models.website import WebsitePage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.db.models.corpus import Corpus

logger = logging.getLogger(__name__)


async def archive_pages(
    session: "AsyncSession",
    vector_store: Any,
    corpus: "Corpus",
    pages: Sequence[WebsitePage],
) -> int:
    """Deactivate the active version of every page's document; returns how many versions were
    archived. Pages without a document are skipped. The caller commits."""
    ids = [p.document_id for p in pages if p.document_id is not None]
    if not ids:
        return 0
    # Forget the fingerprint too: the records pipeline skips a record whose fingerprint matches
    # its document, so a page learned again later would get no new active version.
    documents = (
        (await session.execute(select(Document).where(Document.id.in_(ids)))).scalars().all()
    )
    for document in documents:
        document.content_fingerprint = None
    versions = (
        (
            await session.execute(
                select(DocumentVersion).where(
                    DocumentVersion.document_id.in_(ids), DocumentVersion.status == "active"
                )
            )
        )
        .scalars()
        .all()
    )
    if not versions:
        await session.flush()
        return 0
    collection = corpus.qdrant_collection or "chunks"
    dual = await dual_write_collection(session, corpus.id)
    for version in versions:
        version.status = "inactive"
        await vector_store.delete_by_filter(collection, {"document_version_id": str(version.id)})
        await dual_delete(vector_store, dual, {"document_version_id": str(version.id)})
    await session.flush()
    await _invalidate_cache(corpus)
    return len(versions)


async def _invalidate_cache(corpus: "Corpus") -> None:
    """A page that was taken out must stop appearing in cached answers. Best effort."""
    try:
        from callobuzz_rag.core.config import get_settings
        from callobuzz_rag.retrieval.query_cache import QueryResponseCache

        cache = QueryResponseCache(get_settings())
        await cache.invalidate_corpus(str(corpus.id))
        await cache.close()
    except Exception:
        logger.warning("Could not invalidate the query cache after archiving", exc_info=True)
