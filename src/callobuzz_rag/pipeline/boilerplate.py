"""Drop page furniture that repeats across the documents of a knowledge base.

A site's pages share a lot that is not their content: a disclaimer, a call to action,
"Follow us", a delivery notice. Left in, those lines fill the index with near-identical
passages, so a question about delivery is answered by fifty copies of one footer, and every
page of the site looks like a near-duplicate of every other.

This step runs after parsing and before chunking, on the parsed elements, so it is the same
for every parser and for text records.

Rule. A *block* is one paragraph or list item (never a table, code, heading or caption).
Its hash is `text_hash` of its text. For a new document, the number of OTHER documents of
the knowledge base that already hold the same block hash is looked up; at or above
`min_docs` the block is dropped. Every candidate block's hash is then stored for the
document, dropped ones included, so the count keeps rising. The earliest documents keep
their copy (the count had not reached the limit): nothing is pruned afterwards, so the
knowledge base always holds at least one searchable copy and no stored document is
rewritten. Counts come from `kb_block_hashes` rows, so deleting a document lowers them.

Safeguards. A block longer than `max_chars` is never judged (long repeats are handled by
the duplicate-passage collapse at search time) and its hash is not stored. If dropping
would remove more than half of a document's tokens, or all of it, nothing is dropped and
a warning says so: a page that is mostly shared text is still that page's content.

The hashes are written in the ingest job's transaction, so two ingests running at the same
moment do not see each other's rows and the limit is approximate. That is acceptable.
"""

import logging
import uuid
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.db.models.kb_block_hash import KbBlockHash
from callobuzz_rag.parsing.models import ElementType, ParsedDocument, ParsedElement
from callobuzz_rag.pipeline.text_hash import text_hash

logger = logging.getLogger(__name__)

CANDIDATE_TYPES = frozenset({ElementType.PARAGRAPH, ElementType.LIST_ITEM})

# Rows per INSERT; keeps one statement far below the driver's parameter limit.
_INSERT_BATCH = 2000

block_hash = text_hash


class BlockHashStore(Protocol):
    """Where block hashes live. `SqlBlockHashStore` in production; a dict in unit tests."""

    async def counts(
        self, corpus_id: uuid.UUID, document_id: uuid.UUID, hashes: Iterable[str]
    ) -> Mapping[str, int]:
        """For each hash, how many documents OTHER than `document_id` hold it."""

    async def replace(
        self, corpus_id: uuid.UUID, document_id: uuid.UUID, hashes: Iterable[str]
    ) -> None:
        """The document's rows become exactly `hashes`."""


class SqlBlockHashStore:
    """`BlockHashStore` over `kb_block_hashes`, in the caller's session and transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def counts(
        self, corpus_id: uuid.UUID, document_id: uuid.UUID, hashes: Iterable[str]
    ) -> dict[str, int]:
        wanted = sorted(set(hashes))
        if not wanted:
            return {}
        found: dict[str, int] = {}
        for start in range(0, len(wanted), _INSERT_BATCH):
            rows = await self._session.execute(
                select(KbBlockHash.block_hash, func.count())
                .where(
                    KbBlockHash.corpus_id == corpus_id,
                    KbBlockHash.block_hash.in_(wanted[start : start + _INSERT_BATCH]),
                    KbBlockHash.document_id != document_id,
                )
                .group_by(KbBlockHash.block_hash)
            )
            found.update({row[0]: int(row[1]) for row in rows.all()})
        return found

    async def replace(
        self, corpus_id: uuid.UUID, document_id: uuid.UUID, hashes: Iterable[str]
    ) -> None:
        await self._session.execute(
            delete(KbBlockHash).where(
                KbBlockHash.corpus_id == corpus_id, KbBlockHash.document_id == document_id
            )
        )
        rows = [
            {"corpus_id": corpus_id, "block_hash": h, "document_id": document_id}
            for h in sorted(set(hashes))
        ]
        for start in range(0, len(rows), _INSERT_BATCH):
            await self._session.execute(
                pg_insert(KbBlockHash)
                .values(rows[start : start + _INSERT_BATCH])
                .on_conflict_do_nothing()
            )


@dataclass
class BoilerplateOutcome:
    dropped: int = 0
    dropped_hashes: set[str] = field(default_factory=set)
    # Every candidate block that at least one OTHER document holds: shared text, dropped or
    # not. The near-duplicate check leaves chunks made only of these out of its sample.
    shared_hashes: set[str] = field(default_factory=set)
    warning: str | None = None


def boilerplate_enabled(settings: Any, corpus: Any) -> bool:
    """Whether the step runs: the engine setting is on and the knowledge base has not
    opted out (`retrieval_config.boilerplate_strip = false`). No settings, no step."""
    if settings is None or not getattr(settings, "boilerplate_strip_enabled", False):
        return False
    config = getattr(corpus, "retrieval_config", None) or {}
    return config.get("boilerplate_strip") is not False


def candidate_hash(element: ParsedElement, max_chars: int) -> str | None:
    """The block hash of a paragraph or list item that may be judged, else None."""
    if element.type not in CANDIDATE_TYPES:
        return None
    text = element.text.strip()
    if not text or len(text) > max_chars or not any(ch.isalnum() for ch in text):
        return None
    return block_hash(text)


def is_shared_chunk(text: str, shared: Collection[str], max_chars: int) -> bool:
    """Whether a chunk's text is made only of blocks in `shared` (hashes of paragraphs other
    documents hold). A block counts when its paragraph, or each of its lines (a list), is
    shared. An empty chunk is not."""
    if not shared:
        return False
    blocks = [b.strip() for b in text.split(chr(10) * 2) if b.strip()]
    if not blocks:
        return False

    def held(piece: str) -> bool:
        h = candidate_hash(ParsedElement(type=ElementType.PARAGRAPH, text=piece), max_chars)
        return h is not None and h in shared

    return all(
        held(block) or all(held(line) for line in block.split(chr(10)) if line.strip())
        for block in blocks
    )


async def apply_boilerplate(
    store: BlockHashStore,
    corpus_id: uuid.UUID,
    document_id: uuid.UUID,
    parsed: ParsedDocument,
    *,
    min_docs: int,
    max_chars: int,
    count_tokens: Callable[[str], int],
) -> tuple[ParsedDocument, BoilerplateOutcome]:
    """`parsed` without the blocks that `min_docs` other documents already hold, and what
    was done. The document's own rows are replaced with the hashes of all its candidates."""
    hashes = [candidate_hash(e, max_chars) for e in parsed.elements]
    candidates = {h for h in hashes if h is not None}
    counts = await store.counts(corpus_id, document_id, candidates) if candidates else {}

    doomed = {h for h in candidates if counts.get(h, 0) >= min_docs}
    outcome = BoilerplateOutcome(shared_hashes={h for h in candidates if counts.get(h, 0) >= 1})
    kept = parsed
    if doomed:
        drop = [h is not None and h in doomed for h in hashes]
        total = sum(count_tokens(e.text) for e in parsed.elements if e.text)
        removed = sum(
            count_tokens(e.text) for e, gone in zip(parsed.elements, drop, strict=True) if gone
        )
        if removed >= total or removed * 2 > total:
            outcome.warning = (
                "Repeated text kept: removing it would have taken out more than half of "
                "this document"
            )
            logger.info(
                "Boilerplate step kept %d repeated blocks of %s: %d of %d tokens",
                sum(drop),
                parsed.source_filename,
                removed,
                total,
            )
        else:
            survivors = [e for e, gone in zip(parsed.elements, drop, strict=True) if not gone]
            outcome.dropped = len(parsed.elements) - len(survivors)
            outcome.dropped_hashes = {
                h for h, gone in zip(hashes, drop, strict=True) if gone and h is not None
            }
            outcome.warning = (
                f"{outcome.dropped} block{'' if outcome.dropped == 1 else 's'} stripped"
            )
            kept = replace(parsed, elements=survivors)

    await store.replace(corpus_id, document_id, candidates)
    return kept, outcome
