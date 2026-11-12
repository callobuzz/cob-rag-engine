"""Hybrid retrieval: dense + sparse search with RRF fusion.

Orchestrates the full retrieval pipeline:
1. Preprocess query (language detection, stop-words, tokenization)
2. Embed query (with Redis cache)
3. Generate sparse query vector
4. Execute parallel dense + sparse searches against Qdrant
5. Merge results via Reciprocal Rank Fusion (RRF)
6. Optionally expand with parent chunk context
7. Fetch chunk text from PostgreSQL

All providers are injected via constructor (dependency injection).
"""

import asyncio
import logging
import uuid
from collections.abc import Coroutine
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import RetrievalError
from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.embeddings.base import EmbeddingProvider
from callobuzz_rag.retrieval.dedupe import collapse_duplicate_passages, overfetch
from callobuzz_rag.retrieval.embedding_cache import QueryEmbeddingCache
from callobuzz_rag.retrieval.fusion import FusionStrategy, create_fusion_strategy
from callobuzz_rag.retrieval.models import RetrievalResult, SearchFilters
from callobuzz_rag.retrieval.sparse import query_to_sparse_vector
from callobuzz_rag.vectordb.base import SearchResult, SparseVector, VectorStorageProvider

logger = logging.getLogger(__name__)


class HybridSearcher:
    """Hybrid dense + sparse retriever with RRF fusion.

    Executes dense and sparse searches in parallel against Qdrant,
    merges results via Reciprocal Rank Fusion, and enriches with
    chunk text from PostgreSQL.
    """

    def __init__(
        self,
        vector_store: VectorStorageProvider,
        embedding_provider: EmbeddingProvider,
        embedding_cache: QueryEmbeddingCache,
        session: AsyncSession,
        settings: Settings,
        collection: str | None = None,
        fusion_strategy: FusionStrategy | None = None,
    ) -> None:
        self._vector_store = vector_store
        self._embedding_provider = embedding_provider
        self._embedding_cache = embedding_cache
        self._session = session
        self._top_k_dense = settings.retrieval_top_k_dense
        self._top_k_sparse = settings.retrieval_top_k_sparse
        self._collection = collection or settings.qdrant_collection
        # `is True`: a settings object that does not carry the field (a test double) is off.
        self._collapse_duplicates = getattr(settings, "collapse_duplicates", False) is True
        self._fusion_strategy = (
            fusion_strategy
            if fusion_strategy is not None
            else create_fusion_strategy(settings.fusion_strategy, rrf_k=settings.rrf_k)
        )

    async def search(
        self,
        query: str,
        filters: SearchFilters,
        top_k_dense: int | None = None,
        top_k_sparse: int | None = None,
        expand_parents: bool = False,
        collapse_duplicates: bool | None = None,
    ) -> list[RetrievalResult]:
        """Execute hybrid retrieval.

        Args:
            query: Raw user query string.
            filters: Required filters (corpus_id) and optional metadata filters.
            top_k_dense: Override default dense search limit.
            top_k_sparse: Override default sparse search limit.
            expand_parents: If True, prepend each child match's parent chunk text. Off by
                default: before 2026-09-30 ingest never stored parent links, so expansion
                never ran, and it stays off to keep retrieval output unchanged until it is
                deliberately evaluated (ranking is unaffected either way; only text grows).
            collapse_duplicates: Show a passage stored in several documents once (the
                best-scoring copy, with the others in `also_in`). None: the engine setting
                `RAG_COLLAPSE_DUPLICATES`. Both searches ask for 50% more candidates when
                on, so the top-k is still whole after copies merge.

        Returns:
            List of RetrievalResult sorted by RRF score (descending).

        Raises:
            RetrievalError: If query embedding fails.
        """
        collapse = self._collapse_duplicates if collapse_duplicates is None else collapse_duplicates
        k_dense = overfetch(self._top_k_dense if top_k_dense is None else top_k_dense, collapse)
        k_sparse = overfetch(self._top_k_sparse if top_k_sparse is None else top_k_sparse, collapse)
        qdrant_filters = filters.to_qdrant_filters()

        # 1. Embed query (with cache)
        try:
            embedding = await self._embedding_cache.get_or_embed(
                query, self._embedding_provider, model=self._embedding_provider.model_id
            )
        except Exception as e:
            raise RetrievalError(f"Query embedding failed: {e}") from e

        # 2. Sparse query vector, through the same analyzer as ingest
        sparse_data = query_to_sparse_vector(query)

        # 3. Execute dense and sparse searches in parallel
        sparse_vec: SparseVector | None = None
        if not sparse_data.is_empty():
            sparse_vec = SparseVector(
                indices=sparse_data.indices,
                values=sparse_data.values,
            )

        dense_results, sparse_results = await self._parallel_search(
            query_vector=embedding.vector,
            sparse_vector=sparse_vec,
            k_dense=k_dense,
            k_sparse=k_sparse,
            filters=qdrant_filters,
        )

        # 4. Merge via pluggable fusion strategy (RRF default)
        merged = self._fusion_strategy.fuse([dense_results, sparse_results])
        logger.debug(
            "Fusion merge: %d dense + %d sparse -> %d merged",
            len(dense_results),
            len(sparse_results),
            len(merged),
        )

        if not merged:
            return []

        # 4b. One result per distinct passage (Qdrant has already applied the filters)
        also_in: dict[str, list[dict[str, str | None]]] = {}
        if collapse:
            merged, also_in = await collapse_duplicate_passages(self._session, merged)

        # Build rank lookups for debugging info
        dense_ranks = {r.id: i + 1 for i, r in enumerate(dense_results)}
        sparse_ranks = {r.id: i + 1 for i, r in enumerate(sparse_results)}

        # 5. Fetch chunk text from DB
        chunk_ids = [m.payload.get("chunk_id", m.id) for m in merged]
        chunks_by_id = await self._fetch_chunks(chunk_ids)

        # 6. Build RetrievalResult list
        results: list[RetrievalResult] = []
        for item in merged:
            chunk_id = item.payload.get("chunk_id", item.id)
            chunk = chunks_by_id.get(chunk_id)
            if chunk is None:
                logger.warning("Chunk %s in Qdrant but missing from PostgreSQL", chunk_id)
                continue

            result = RetrievalResult(
                chunk_id=chunk_id,
                text=chunk.text,
                score=item.score,
                corpus_id=item.payload.get("corpus_id", ""),
                document_id=item.payload.get("document_id", ""),
                document_version_id=item.payload.get("document_version_id", ""),
                page=item.payload.get("page"),
                is_table=item.payload.get("is_table", False),
                is_code=item.payload.get("is_code", False),
                language=item.payload.get("language"),
                keywords=item.payload.get("keywords", []),
                dense_rank=dense_ranks.get(item.id),
                sparse_rank=sparse_ranks.get(item.id),
                also_in=also_in.get(item.id, []),
            )
            results.append(result)

        # 7. Optional parent expansion
        if expand_parents:
            results = await self._expand_parents(results, chunks_by_id)

        return results

    async def _parallel_search(
        self,
        query_vector: list[float],
        sparse_vector: SparseVector | None,
        k_dense: int,
        k_sparse: int,
        filters: dict[str, Any],
    ) -> tuple[list[SearchResult], list[SearchResult]]:
        """Run dense and sparse searches in parallel.

        If one search fails, the other still returns results.
        If sparse_vector is None (empty keywords), sparse search is skipped.
        """
        dense_task = asyncio.create_task(
            self._safe_search(
                self._vector_store.search(self._collection, query_vector, k_dense, filters),
                "dense",
            )
        )

        if sparse_vector is not None:
            sparse_task = asyncio.create_task(
                self._safe_search(
                    self._vector_store.search_sparse(
                        self._collection, sparse_vector, k_sparse, filters
                    ),
                    "sparse",
                )
            )
            dense_results, sparse_results = await asyncio.gather(dense_task, sparse_task)
        else:
            logger.debug("Sparse vector empty — skipping sparse search")
            dense_results = await dense_task
            sparse_results = []

        if not dense_results and not sparse_results:
            logger.warning("Both dense and sparse searches returned no results")

        return dense_results, sparse_results

    @staticmethod
    async def _safe_search(
        coro: "Coroutine[Any, Any, list[SearchResult]]",
        search_type: str,
    ) -> list[SearchResult]:
        """Execute a search with error handling.

        Returns empty list on failure (allows the other search to proceed).
        """
        try:
            results = await coro
            return results
        except Exception as e:
            logger.error("%s search failed: %s", search_type, e, exc_info=True)
            return []

    async def _fetch_chunks(
        self,
        chunk_ids: list[str],
    ) -> dict[str, Chunk]:
        """Fetch chunk records from PostgreSQL by ID."""
        if not chunk_ids:
            return {}

        uuids = []
        for cid in chunk_ids:
            try:
                uuids.append(uuid.UUID(cid))
            except ValueError:
                logger.warning("Invalid chunk_id format: %s", cid)

        if not uuids:
            return {}

        stmt = select(Chunk).where(Chunk.id.in_(uuids))
        result = await self._session.execute(stmt)
        chunks = result.scalars().all()

        return {str(chunk.id): chunk for chunk in chunks}

    async def _expand_parents(
        self,
        results: list[RetrievalResult],
        chunks_by_id: dict[str, Chunk],
    ) -> list[RetrievalResult]:
        """Expand child chunks with parent context.

        For each result that has a parent_chunk_id, fetch the parent
        chunk text and prepend it to the child text.
        """
        parent_ids_needed: set[str] = set()
        for result in results:
            chunk = chunks_by_id.get(result.chunk_id)
            if chunk and chunk.parent_chunk_id is not None:
                parent_id = str(chunk.parent_chunk_id)
                if parent_id not in chunks_by_id:
                    parent_ids_needed.add(parent_id)

        if parent_ids_needed:
            parent_chunks = await self._fetch_chunks(list(parent_ids_needed))
            chunks_by_id.update(parent_chunks)

        for result in results:
            chunk = chunks_by_id.get(result.chunk_id)
            if chunk and chunk.parent_chunk_id is not None:
                parent_id = str(chunk.parent_chunk_id)
                parent = chunks_by_id.get(parent_id)
                if parent and parent.text:
                    result.text = (
                        f"[Parent context]\n{parent.text}\n\n[Matched chunk]\n{result.text}"
                    )
                    result.has_parent_context = True

        return results
