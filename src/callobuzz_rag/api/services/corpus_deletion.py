"""Deleting knowledge bases: database rows first, then vectors and files.

Shared by `DELETE /api/v1/corpora/{id}` and the tenant lifecycle (hard delete and
decline, accounts A4), which deletes every corpus a tenant owns before the tenant itself
(`corpora.tenant_id` is RESTRICT, accounts A1).

Order matters. The database goes first, in one SQL statement, and the vector collection
and stored files only after it commits: a crash or timeout in between then leaves an
orphaned collection (harmless, just disk) rather than a corpus whose rows survive while
its vectors are gone, which would fail every search on it. The delete runs in SQL so the
database's ON DELETE CASCADE removes the children; the ORM cascade loaded every
document, version and chunk into memory first, which took minutes on a large corpus.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete

from callobuzz_rag.db.models.corpus import Corpus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredCorpus:
    """What is left to clean up outside Postgres once a corpus row is gone."""

    id: uuid.UUID
    qdrant_collection: str


async def delete_corpus_rows(
    session: "AsyncSession", corpus_ids: Sequence[uuid.UUID]
) -> list[StoredCorpus]:
    """Delete the corpora (their children cascade in the database). Not committed.

    Returns the deleted corpora's ids and collections, for `cleanup_corpus_storage`
    after the caller commits.
    """
    if not corpus_ids:
        return []
    result = await session.execute(
        delete(Corpus)
        .where(Corpus.id.in_(list(corpus_ids)))
        .returning(Corpus.id, Corpus.qdrant_collection)
    )
    return [StoredCorpus(id=cid, qdrant_collection=coll) for cid, coll in result.all()]


async def cleanup_corpus_storage(app_state: Any, stored: Sequence[StoredCorpus]) -> None:
    """Best effort, after the commit: each corpus's Qdrant collection and stored files.

    Never raises. What cannot be deleted is logged; files are queued, bounded, never
    deleted inline (a slow S3 must not hold the request) and anything left over is for
    `python -m callobuzz_rag.storage.gc`.
    """
    if not stored:
        return
    vector_store = getattr(app_state, "vector_store", None)
    for corpus in stored:
        if vector_store is not None:
            try:
                await vector_store.delete_collection(corpus.qdrant_collection)
                logger.info(
                    "Deleted Qdrant collection %s for corpus %s",
                    corpus.qdrant_collection,
                    corpus.id,
                )
            except Exception as e:
                logger.warning(
                    "Qdrant collection %s left behind for deleted corpus %s: %s",
                    corpus.qdrant_collection,
                    corpus.id,
                    e,
                )
        # Document files, and the Content Portal's staged uploads (`build_portal_storage_key`),
        # whose rows the corpus row cascaded away.
        try:
            from callobuzz_rag.storage.cleanup import schedule_prefix_cleanup

            await schedule_prefix_cleanup(
                app_state.settings.s3_bucket,
                [f"corpus/{corpus.id}/", f"portal/{corpus.id}/"],
                reason=f"corpus {corpus.id} deleted",
            )
        except Exception as e:
            logger.warning("Could not queue file cleanup for deleted corpus %s: %s", corpus.id, e)
        logger.info("Corpus deleted: id=%s", corpus.id)
