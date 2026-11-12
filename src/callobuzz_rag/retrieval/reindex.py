"""Rebuild corpora's vectors in place from the chunk text stored in Postgres.

    python -m callobuzz_rag.retrieval.reindex                 # dry run: what would change
    python -m callobuzz_rag.retrieval.reindex --apply         # keyword (BM25) vectors
    python -m callobuzz_rag.retrieval.reindex --apply --dense # ... and re-embed too
    python -m callobuzz_rag.retrieval.reindex --apply --corpus <id> [--corpus <id> ...]
    python -m callobuzz_rag.retrieval.reindex --apply --rebuild --corpus <id>

Run it after changing how chunks are indexed: the keyword analyzer
(`RAG_SPARSE_*`), `RAG_INDEX_HEADING_CONTEXT`, or the passage prefix of the
embedding model. Keyword vectors are cheap to rebuild — no model is involved.
`--dense` also re-embeds every chunk with the corpus's current embedding model, which
costs one embedding call per chunk; switching to a DIFFERENT model is an embedding
migration instead (`/api/v1/corpora/{id}/migrations`), which builds a new collection.

Safe on a live system: vectors are replaced point by point with the payload left
alone, so a query mid-run sees each chunk either before or after, never missing. It is
idempotent; an interrupted run is finished by running it again.

`--rebuild` is disaster recovery: it drops the corpus's Qdrant collection and writes it
again from Postgres — vectors AND payloads, including document metadata — for a
collection that was lost or corrupted. Searches on the corpus return nothing until it
finishes. Metadata of documents ingested before it was stored in Postgres (engine
versions before 0.2) cannot be restored this way.

    python -m callobuzz_rag.retrieval.reindex --parents           # dry run: links to recover
    python -m callobuzz_rag.retrieval.reindex --parents --apply   # store them

    python -m callobuzz_rag.retrieval.reindex --payload-hash            # dry run
    python -m callobuzz_rag.retrieval.reindex --payload-hash --apply    # fill text_hash
    python -m callobuzz_rag.retrieval.reindex --rehash-blocks --apply   # seed the furniture counts

`--payload-hash` fills `chunks.text_hash` (Postgres) and the `text_hash` payload field of
the chunk's point (Qdrant) for chunks written before they existed, with no re-embedding.
Search then shows a passage stored in several documents once. A chunk written before the
column carries its overlap in its text, so its hash differs from a new chunk's of the same
passage; re-ingesting the document gives it the exact hash. Idempotent.

`--rehash-blocks` fills `kb_block_hashes` from the corpus's active chunks (one row per
document and paragraph hash), so the next ingests recognise page furniture that is already
in the knowledge base instead of waiting for the counts to build up. It does not change any
stored document. Best effort: a paragraph is found only where the chunker kept it on a
line of its own. Idempotent.

`--parents` touches Postgres only. Ingest used to store no parent link at all (it
linked ids the database had not assigned yet), so documents ingested by earlier builds
have none and parent expansion never ran on them. It recovers each version's links
by the chunker's own rule — the one the Content Portal uses on such versions — and
leaves any version that already has links alone, so it is idempotent.
"""

import argparse
import asyncio
import logging
import sys
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.chunking.index_text import index_text
from callobuzz_rag.core.config import Settings, get_settings
from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.db.models.kb_block_hash import KbBlockHash
from callobuzz_rag.embeddings.base import EmbeddingProvider
from callobuzz_rag.parsing.models import ElementType, ParsedElement
from callobuzz_rag.pipeline.boilerplate import SqlBlockHashStore, candidate_hash
from callobuzz_rag.pipeline.text_hash import text_hash
from callobuzz_rag.retrieval.sparse import text_to_sparse_vector
from callobuzz_rag.vectordb.base import VectorPoint, VectorStorageProvider

logger = logging.getLogger(__name__)


def _active_chunks(corpus_id: uuid.UUID) -> Any:
    return (
        select(Chunk)
        .join(DocumentVersion, Chunk.document_version_id == DocumentVersion.id)
        .where(Chunk.corpus_id == corpus_id, DocumentVersion.status == "active")
    )


async def count_active_chunks(session: AsyncSession, corpus_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(_active_chunks(corpus_id).subquery())
    return int((await session.execute(stmt)).scalar_one())


async def reindex_corpus(
    session: AsyncSession,
    corpus: Corpus,
    vector_store: VectorStorageProvider,
    *,
    embedder: EmbeddingProvider | None = None,
    batch_size: int = 256,
) -> int:
    """Rewrite every active chunk's keyword vector (and dense vector, given an embedder).

    Returns the number of chunks rewritten. Pages by chunk id, so rows added during
    the run are picked up and none is visited twice.
    """
    done = 0
    last_id: uuid.UUID | None = None
    while True:
        stmt = _active_chunks(corpus.id).order_by(Chunk.id).limit(batch_size)
        if last_id is not None:
            stmt = stmt.where(Chunk.id > last_id)
        chunks: list[Chunk] = list((await session.execute(stmt)).scalars().all())
        if not chunks:
            return done
        texts = [index_text(c.text, c.structure_path) for c in chunks]
        vectors: list[list[float]] = [[] for _ in chunks]
        if embedder is not None:
            vectors = [e.vector for e in await embedder.embed_texts(texts)]
        points = []
        for chunk, text, vector in zip(chunks, texts, vectors, strict=True):
            sparse = text_to_sparse_vector(text)
            points.append(
                VectorPoint(
                    id=str(chunk.id),
                    vector=vector,
                    payload={},
                    sparse_vector=None if sparse.is_empty() else sparse.to_sparse_vector(),
                )
            )
        await vector_store.update_vectors(corpus.qdrant_collection, points)
        done += len(chunks)
        last_id = chunks[-1].id
        logger.info("reindex %s: %d chunks", corpus.id, done)


async def rebuild_corpus(
    session: AsyncSession,
    corpus: Corpus,
    vector_store: VectorStorageProvider,
    embedder: EmbeddingProvider,
    dimensions: int,
    *,
    batch_size: int = 256,
) -> int:
    """Recreate the corpus's collection from Postgres: every active chunk, embedded and
    written with its full payload. Returns the number of chunks written."""
    from sqlalchemy.orm import selectinload

    from callobuzz_rag.pipeline.payload import chunk_payload

    await vector_store.delete_collection(corpus.qdrant_collection)
    await vector_store.create_collection(corpus.qdrant_collection, dimensions)
    done = 0
    last_id: uuid.UUID | None = None
    while True:
        stmt = (
            _active_chunks(corpus.id)
            .options(
                selectinload(Chunk.document_version).selectinload(DocumentVersion.document),
                selectinload(Chunk.metadata_record),
            )
            .order_by(Chunk.id)
            .limit(batch_size)
        )
        if last_id is not None:
            stmt = stmt.where(Chunk.id > last_id)
        chunks: list[Chunk] = list((await session.execute(stmt)).scalars().all())
        if not chunks:
            return done
        texts = [index_text(c.text, c.structure_path) for c in chunks]
        embeddings = await embedder.embed_texts(texts)
        points = []
        for chunk, text, emb in zip(chunks, texts, embeddings, strict=True):
            document = chunk.document_version.document
            payload = chunk_payload(
                chunk,
                corpus.id,
                document.id,
                document.doc_metadata,
                chunk.metadata_record.keywords if chunk.metadata_record else None,
            )
            sparse = text_to_sparse_vector(text)
            points.append(
                VectorPoint(
                    id=str(chunk.id),
                    vector=emb.vector,
                    payload=payload.to_dict(),
                    sparse_vector=None if sparse.is_empty() else sparse.to_sparse_vector(),
                )
            )
        await vector_store.upsert(corpus.qdrant_collection, points)
        done += len(chunks)
        last_id = chunks[-1].id
        logger.info("rebuild %s: %d chunks", corpus.id, done)


@dataclass
class ParentLinkBackfill:
    versions: int = 0  # document versions looked at
    already_linked: int = 0  # versions that have stored links; left alone
    recovered_versions: int = 0  # versions with no stored link whose parents were recognised
    links: int = 0  # child -> parent links recovered (written with apply)


async def backfill_parent_links(
    session: AsyncSession, corpus_id: uuid.UUID, *, apply: bool, batch_size: int = 1000
) -> ParentLinkBackfill:
    """Recover the parent links of every version of the corpus that has none stored.

    Every version, not only active ones: restoring an older version brings its chunks
    back. The rule is the Content Portal's (`parent_links`), which is the chunker's own
    (`StructureAwareChunker._add_parent_child`): a parent comes right before at least
    two chunks of its heading path, and its text is exactly theirs, tables and code left
    out, joined by blank lines. The chunker's size cap (two times max tokens) needs no
    check here: a chunk with that text exists only because the chunker made it, and the
    cap in force when it did may not be today's. With `apply` the links are written and
    committed; without it nothing is.
    """
    from callobuzz_rag.content_portal.services.post_ingest_review import parent_links

    stmt = (
        select(
            Chunk.id,
            Chunk.document_version_id,
            Chunk.chunk_index,
            Chunk.text,
            Chunk.structure_path,
            Chunk.is_table,
            Chunk.is_code,
            Chunk.parent_chunk_id,
        )
        .where(Chunk.corpus_id == corpus_id)
        .order_by(Chunk.document_version_id, Chunk.chunk_index, Chunk.id)
        .execution_options(yield_per=5000)
    )
    counts = ParentLinkBackfill()
    found: dict[uuid.UUID, uuid.UUID] = {}

    def settle(rows: list[Any]) -> None:
        counts.versions += 1
        if any(r.parent_chunk_id is not None for r in rows):
            counts.already_linked += 1
            return
        links = parent_links(rows)
        if links:
            counts.recovered_versions += 1
            counts.links += len(links)
            found.update(links)

    version: uuid.UUID | None = None
    rows: list[Any] = []
    # Streamed, one version at a time: a corpus can hold hundreds of thousands of chunks.
    result = await session.stream(stmt)
    async for row in result:
        if row.document_version_id != version and rows:
            settle(rows)
            rows = []
        version = row.document_version_id
        rows.append(row)
    if rows:
        settle(rows)

    if apply and found:
        pairs = list(found.items())
        for start in range(0, len(pairs), batch_size):
            await session.execute(
                update(Chunk),
                [{"id": c, "parent_chunk_id": p} for c, p in pairs[start : start + batch_size]],
            )
        await session.commit()
    return counts


@dataclass
class TextHashBackfill:
    chunks: int = 0  # chunks without a text_hash
    active: int = 0  # of those, in an active version (their points get the payload field)


async def backfill_text_hashes(
    session: AsyncSession,
    corpus: Corpus,
    vector_store: VectorStorageProvider,
    *,
    apply: bool,
    batch_size: int = 500,
) -> TextHashBackfill:
    """Give every chunk of the corpus that has no `text_hash` one, in Postgres and, for
    chunks of active versions, on the Qdrant point. No vector is touched or re-embedded.

    Pages by chunk id and commits per page, so an interrupted run is finished by running
    it again (chunks that have a hash are no longer selected).
    """
    counts = TextHashBackfill()
    last_id: uuid.UUID | None = None
    is_active = exists().where(
        DocumentVersion.id == Chunk.document_version_id, DocumentVersion.status == "active"
    )
    while True:
        stmt = (
            select(Chunk.id, Chunk.text, is_active.label("active"))
            .where(Chunk.corpus_id == corpus.id, Chunk.text_hash.is_(None))
            .order_by(Chunk.id)
            .limit(batch_size)
        )
        if last_id is not None:
            stmt = stmt.where(Chunk.id > last_id)
        rows = list((await session.execute(stmt)).all())
        if not rows:
            return counts
        counts.chunks += len(rows)
        counts.active += sum(1 for r in rows if r.active)
        last_id = rows[-1].id
        if not apply:
            continue
        hashes = {r.id: text_hash(r.text) for r in rows}
        await session.execute(
            update(Chunk), [{"id": cid, "text_hash": h} for cid, h in hashes.items()]
        )
        await vector_store.set_payloads(
            corpus.qdrant_collection,
            {str(r.id): {"text_hash": hashes[r.id]} for r in rows if r.active},
        )
        await session.commit()
        logger.info("payload-hash %s: %d chunks", corpus.id, counts.chunks)


@dataclass
class BlockRehash:
    documents: int = 0  # documents with at least one paragraph hash
    hashes: int = 0  # (document, paragraph hash) rows


def paragraph_lines(text: str, previous: str | None) -> list[str]:
    """The lines of a chunk's text that can be paragraphs: not the overlap taken from the
    previous chunk (its first line, when that is the end of `previous`)."""
    lines = [line.strip() for line in text.split(chr(10)) if line.strip()]
    if previous is not None and lines and lines[0] in previous:
        lines = lines[1:]
    return lines


async def rehash_blocks(
    session: AsyncSession, corpus: Corpus, *, apply: bool, max_chars: int
) -> BlockRehash:
    """Fill `kb_block_hashes` from the corpus's active chunks (see the module docstring).

    Only documents with no rows yet are seeded. A document ingested since paragraph hashing
    shipped already has the parser's exact hashes; this pass rebuilds hashes from chunk
    text, a line-based approximation, and must not overwrite the exact ones.
    """
    from sqlalchemy.orm import aliased

    child = aliased(Chunk)
    stmt = (
        select(
            DocumentVersion.document_id,
            Chunk.document_version_id,
            Chunk.chunk_index,
            Chunk.text,
            Chunk.structure_path,
        )
        .join(DocumentVersion, Chunk.document_version_id == DocumentVersion.id)
        .where(
            Chunk.corpus_id == corpus.id,
            DocumentVersion.status == "active",
            Chunk.is_table.is_(False),
            Chunk.is_code.is_(False),
            ~exists().where(child.parent_chunk_id == Chunk.id),  # parents repeat their children
            ~exists().where(  # documents that already have exact hashes keep them
                KbBlockHash.corpus_id == Chunk.corpus_id,
                KbBlockHash.document_id == DocumentVersion.document_id,
            ),
        )
        .order_by(DocumentVersion.document_id, Chunk.document_version_id, Chunk.chunk_index)
        .execution_options(yield_per=2000)
    )
    store = SqlBlockHashStore(session)
    done = BlockRehash()
    hashes: set[str] = set()
    document: uuid.UUID | None = None
    previous: tuple[uuid.UUID, list[str] | None, str] | None = None
    # Writes wait until the cursor is drained: inserting on the session that is streaming
    # would interleave statements with an open server-side cursor.
    pending: list[tuple[uuid.UUID, set[str]]] = []

    async def settle() -> None:
        if document is None or not hashes:
            return
        done.documents += 1
        done.hashes += len(hashes)
        pending.append((document, hashes))

    result = await session.stream(stmt)
    async for row in result:
        if row.document_id != document:
            await settle()
            document, hashes, previous = row.document_id, set(), None
        before = previous[2] if previous and previous[0] == row.document_version_id else None
        if before is not None and previous is not None and previous[1] != row.structure_path:
            before = None  # overlap never crosses a section
        for line in paragraph_lines(row.text, before):
            h = candidate_hash(ParsedElement(type=ElementType.PARAGRAPH, text=line), max_chars)
            if h is not None:
                hashes.add(h)
        previous = (row.document_version_id, row.structure_path, row.text)
    await settle()
    if apply:
        for doc_id, doc_hashes in pending:
            await store.replace(corpus.id, doc_id, doc_hashes)
        await session.commit()
    return done


async def _run_hashes(
    settings: Settings, corpus_ids: list[str], apply: bool, *, blocks: bool
) -> int:
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    vector_store = QdrantVectorProvider(settings)
    failures = 0
    try:
        async with session_factory() as session:
            corpora = await _corpora(session, corpus_ids)
            if corpora is None:
                return 2
            for corpus in corpora:
                try:
                    if blocks:
                        b = await rehash_blocks(
                            session, corpus, apply=apply, max_chars=settings.boilerplate_max_chars
                        )
                        verb = "stored" if apply else "would store"
                        print(
                            f"{corpus.id}  {corpus.name!r}: {verb} {b.hashes} paragraph hashes "
                            f"for {b.documents} documents"
                        )
                    else:
                        h = await backfill_text_hashes(session, corpus, vector_store, apply=apply)
                        verb = "hashed" if apply else "would hash"
                        print(
                            f"{corpus.id}  {corpus.name!r}: {verb} {h.chunks} chunks "
                            f"({h.active} in active versions)"
                        )
                except Exception as e:  # one corpus failing must not stop the pass
                    await session.rollback()
                    failures += 1
                    print(f"{corpus.id}  {corpus.name!r}: FAILED — {e}")
        if not apply:
            print("dry run — nothing written. Re-run with --apply.")
        return 1 if failures else 0
    finally:
        await vector_store.close()
        await engine.dispose()


async def _run_parents(settings: Settings, corpus_ids: list[str], apply: bool) -> int:
    from callobuzz_rag.db.engine import create_engine, create_session_factory

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    failures = 0
    try:
        async with session_factory() as session:
            corpora = await _corpora(session, corpus_ids)
            if corpora is None:
                return 2
            for corpus in corpora:
                try:
                    n = await backfill_parent_links(session, corpus.id, apply=apply)
                except Exception as e:  # one corpus failing must not stop the pass
                    await session.rollback()
                    failures += 1
                    print(f"{corpus.id}  {corpus.name!r}: FAILED — {e}")
                    continue
                verb = "linked" if apply else "would link"
                print(
                    f"{corpus.id}  {corpus.name!r}: {verb} {n.links} chunks to their parents "
                    f"in {n.recovered_versions} of {n.versions} versions "
                    f"({n.already_linked} already linked)"
                )
        if not apply:
            print("dry run — nothing written. Re-run with --apply.")
        return 1 if failures else 0
    finally:
        await engine.dispose()


async def _corpora(session: AsyncSession, corpus_ids: list[str]) -> list[Corpus] | None:
    """The corpora to work on (all when none named), or None after reporting unknown ids."""
    stmt = select(Corpus).order_by(Corpus.created_at)
    if corpus_ids:
        stmt = stmt.where(Corpus.id.in_([uuid.UUID(c) for c in corpus_ids]))
    corpora = list((await session.execute(stmt)).scalars().all())
    if corpus_ids and len(corpora) != len(set(corpus_ids)):
        found = {str(c.id) for c in corpora}
        print(f"unknown corpus id(s): {', '.join(set(corpus_ids) - found)}")
        return None
    return corpora


async def _run(
    settings: Settings, corpus_ids: list[str], dense: bool, apply: bool, rebuild: bool = False
) -> int:
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.embeddings.factory import create_embedding_provider
    from callobuzz_rag.migration.service import effective_desired
    from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    vector_store = QdrantVectorProvider(settings)
    failures = 0
    try:
        async with session_factory() as session:
            corpora = await _corpora(session, corpus_ids)
            if corpora is None:
                return 2
            for corpus in corpora:
                total = await count_active_chunks(session, corpus.id)
                what = (
                    "the whole collection"
                    if rebuild
                    else ("keyword + dense" if dense else "keyword")
                )
                if not apply:
                    print(f"{corpus.id}  {corpus.name!r}: would rebuild {what} for {total} chunks")
                    continue
                desired = effective_desired(corpus, settings)
                embedder = (
                    create_embedding_provider(settings, desired) if dense or rebuild else None
                )
                try:
                    if rebuild:
                        assert embedder is not None
                        done = await rebuild_corpus(
                            session, corpus, vector_store, embedder, desired.dimensions
                        )
                    else:
                        done = await reindex_corpus(
                            session, corpus, vector_store, embedder=embedder
                        )
                    print(f"{corpus.id}  {corpus.name!r}: rebuilt {what} for {done} chunks")
                except Exception as e:  # one corpus failing must not stop the pass
                    failures += 1
                    print(f"{corpus.id}  {corpus.name!r}: FAILED — {e}")
        if not apply:
            print("dry run — nothing written. Re-run with --apply.")
        return 1 if failures else 0
    finally:
        await vector_store.close()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", action="append", default=[], help="corpus id; repeatable")
    parser.add_argument("--dense", action="store_true", help="also re-embed every chunk")
    parser.add_argument("--apply", action="store_true", help="write; without it, a dry run")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="drop and recreate each collection from Postgres (disaster recovery)",
    )
    parser.add_argument(
        "--parents",
        action="store_true",
        help="recover missing parent-chunk links in Postgres instead (no vectors touched)",
    )
    parser.add_argument(
        "--payload-hash",
        action="store_true",
        help="fill text_hash on chunks and points written before it existed (no re-embedding)",
    )
    parser.add_argument(
        "--rehash-blocks",
        action="store_true",
        help="seed the page-furniture counts (kb_block_hashes) from the active chunks",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.payload_hash or args.rehash_blocks:
        if args.payload_hash and args.rehash_blocks:
            parser.error(
                "--payload-hash and --rehash-blocks are separate passes; run one at a time"
            )
        if args.dense or args.rebuild or args.parents:
            parser.error(
                "--payload-hash and --rehash-blocks re-embed nothing; drop the other modes"
            )
        sys.exit(
            asyncio.run(
                _run_hashes(get_settings(), args.corpus, args.apply, blocks=args.rehash_blocks)
            )
        )
    if args.parents:
        if args.dense or args.rebuild:
            parser.error("--parents touches no vectors; drop --dense and --rebuild")
        sys.exit(asyncio.run(_run_parents(get_settings(), args.corpus, args.apply)))
    if args.rebuild and not args.corpus:
        parser.error("--rebuild needs at least one --corpus; it empties each collection first")
    sys.exit(asyncio.run(_run(get_settings(), args.corpus, args.dense, args.apply, args.rebuild)))


if __name__ == "__main__":
    main()
