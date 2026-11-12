"""Show a passage that is stored in several documents once.

The same text turns up in many documents of one knowledge base: a shared policy page, a
pricing table repeated on every product page, a long disclaimer. Every document keeps its
own points (so deleting, restoring, re-indexing or migrating one never touches another),
and each point carries `text_hash` (`pipeline/text_hash.py`) in its payload. After fusion,
results with the same hash are collapsed into the best-scoring one, and the other
documents that hold the passage are listed as `also_in`.

Ties (identical text scores identically) go to the most recently ingested document, then
to the lower document id, so the choice is the same on every call.

Qdrant applies the caller's filters before any of this: with a `document_id`, `category`
or metadata filter only matching points are searched, so a copy in a document the caller
did not ask for can never hide one it did.
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.db.models.document import Document
from callobuzz_rag.vectordb.base import SearchResult

logger = logging.getLogger(__name__)

ALSO_IN_LIMIT = 5

# Fusion scores of identical text agree to the last digits; treat that as a tie.
_SCORE_DIGITS = 9


def overfetch(limit: int, enabled: bool) -> int:
    """`limit` plus 50%, rounded up, so a collapse that merges copies leaves top-k whole."""
    if not enabled:
        return limit
    return limit + (limit + 1) // 2


async def collapse_duplicate_passages(
    session: AsyncSession, results: list[SearchResult]
) -> tuple[list[SearchResult], dict[str, list[dict[str, str | None]]]]:
    """`results` with one entry per distinct passage, and for each kept entry the other
    documents that hold it.

    Returns (kept results in their original order, {kept point id: also_in}). A result
    without a `text_hash` (a chunk stored before the hash existed) is never collapsed.
    The database is read only when there is something to collapse.
    """
    groups: dict[str, list[SearchResult]] = {}
    for result in results:
        text_hash = result.payload.get("text_hash")
        if isinstance(text_hash, str) and text_hash:
            groups.setdefault(text_hash, []).append(result)
    repeated = {h: members for h, members in groups.items() if len(members) > 1}
    if not repeated:
        return results, {}

    doc_ids: set[uuid.UUID] = set()
    for members in repeated.values():
        for member in members:
            try:
                doc_ids.add(uuid.UUID(str(member.payload.get("document_id"))))
            except ValueError:
                continue
    rows = await session.execute(
        select(Document.id, Document.file_name, Document.ingested_at).where(
            Document.id.in_(doc_ids)
        )
    )
    documents: dict[str, tuple[str | None, datetime | None]] = {
        str(row[0]): (row[1], row[2]) for row in rows.all()
    }

    def rank(member: SearchResult) -> tuple[float, float, str]:
        doc_id = str(member.payload.get("document_id"))
        ingested = documents.get(doc_id, (None, None))[1]
        return (
            -round(member.score, _SCORE_DIGITS),
            -(ingested.timestamp() if ingested is not None else 0.0),
            doc_id,
        )

    dropped: set[str] = set()
    also_in: dict[str, list[dict[str, str | None]]] = {}
    for members in repeated.values():
        ordered = sorted(members, key=rank)
        primary = ordered[0]
        primary_doc = str(primary.payload.get("document_id"))
        listed: list[dict[str, str | None]] = []
        seen = {primary_doc}
        for other in ordered[1:]:
            dropped.add(other.id)
            doc_id = str(other.payload.get("document_id"))
            if doc_id in seen or doc_id not in documents:
                continue
            seen.add(doc_id)
            listed.append({"document_id": doc_id, "file_name": documents[doc_id][0]})
        if listed:
            also_in[primary.id] = listed[:ALSO_IN_LIMIT]

    kept = [r for r in results if r.id not in dropped]
    logger.debug("Collapsed %d duplicate passage hit(s)", len(results) - len(kept))
    return kept, also_in
