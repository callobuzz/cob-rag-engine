"""Content similarity detection for document re-ingestion.

Embeds a sample of chunks from a new document and searches for similar
vectors in the same corpus. Returns candidate documents sorted by
average similarity score. Does NOT auto-decide — returns candidates
for the user to review.

A candidate has to be a real near-duplicate, not merely a document of the same site:

- a hit counts only at or above `min_score` (cosine; `RAG_SIMILARITY_MIN_SCORE`, 0.90);
- the sample is spread across the document (first, middle, last), not its first chunks,
  which is where a page's menu and banner sit;
- a document is a candidate only when at least `min_matches` of the sampled chunks (three
  of five) found it, so one shared passage does not make two documents "similar";
- hits on text the ingest has just recognised as page furniture are ignored.

Runs after chunking and after the boilerplate step, so the sample is real content only.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from callobuzz_rag.embeddings.base import EmbeddingProvider
    from callobuzz_rag.vectordb.base import VectorStorageProvider

logger = logging.getLogger(__name__)

_SAMPLE_SIZE = 5
MIN_SCORE = 0.90
MIN_MATCHES = 3


def spread_sample[T](items: Sequence[T], n: int = _SAMPLE_SIZE) -> list[T]:
    """At most `n` items spread evenly over `items`, always including the first and last."""
    if n <= 0 or not items:
        return []
    if len(items) <= n:
        return list(items)
    if n == 1:
        return [items[0]]
    last = len(items) - 1
    picks = sorted({round(i * last / (n - 1)) for i in range(n)})
    return [items[i] for i in picks]


async def find_similar_documents(
    chunks_text: list[str],
    corpus_id: uuid.UUID,
    embedder: EmbeddingProvider,
    vector_store: VectorStorageProvider,
    collection: str,
    exclude_document_id: str | None = None,
    sample_size: int = _SAMPLE_SIZE,
    search_limit: int = 20,
    min_score: float | None = None,
    min_matches: int = MIN_MATCHES,
    ignore_text_hashes: Collection[str] = (),
) -> list[dict[str, Any]]:
    """Find documents in the same corpus with similar content.

    Args:
        chunks_text: Text content of chunks from the new document.
        corpus_id: Corpus to search within.
        embedder: Embedding provider for vectorizing sample chunks.
        vector_store: Vector store for similarity search.
        collection: Qdrant collection name.
        exclude_document_id: Document ID to exclude from results (e.g., self).
        sample_size: Number of chunks to sample, spread across the document (default 5).
        search_limit: Max results per search query (default 20).
        min_score: Lowest cosine score that counts as a match (default 0.90).
        min_matches: How many sampled chunks must match a document (default 3; a document
            with fewer sampled chunks than that needs all of them).
        ignore_text_hashes: Hits whose stored `text_hash` is in here are page furniture.

    Returns:
        List of candidate dicts sorted by similarity (highest first):
        [{"document_id": str, "similarity": float, "match_count": int}]
        where `match_count` is the number of sampled chunks the document matched and
        `similarity` the mean of its best score for each of them.
    """
    if not chunks_text:
        return []

    floor = MIN_SCORE if min_score is None else min_score
    sample = spread_sample(chunks_text, sample_size)
    required = min(min_matches, len(sample))
    ignored = set(ignore_text_hashes)

    try:
        embeddings = await embedder.embed_texts(sample, task="retrieval.passage")
    except Exception:
        logger.warning("Failed to embed sample chunks for similarity detection", exc_info=True)
        return []

    doc_scores: dict[str, list[float]] = {}

    for emb in embeddings:
        try:
            results = await vector_store.search(
                collection=collection,
                query_vector=emb.vector,
                limit=search_limit,
                filters={"corpus_id": str(corpus_id)},
            )
        except Exception:
            logger.warning("Similarity search failed for one sample chunk", exc_info=True)
            continue

        # A document counts once per sampled chunk, by its best hit.
        best: dict[str, float] = {}
        for hit in results:
            doc_id = hit.payload.get("document_id")
            if not doc_id or doc_id == exclude_document_id or hit.score < floor:
                continue
            if hit.payload.get("text_hash") in ignored:
                continue
            if hit.score > best.get(doc_id, -1.0):
                best[doc_id] = hit.score
        for doc_id, score in best.items():
            doc_scores.setdefault(doc_id, []).append(score)

    candidates: list[dict[str, Any]] = [
        {
            "document_id": doc_id,
            "similarity": round(sum(scores) / len(scores), 4),
            "match_count": len(scores),
        }
        for doc_id, scores in doc_scores.items()
        if len(scores) >= required
    ]
    candidates.sort(key=lambda c: c["similarity"], reverse=True)
    return candidates
