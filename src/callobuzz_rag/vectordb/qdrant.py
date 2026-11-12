"""Qdrant vector storage implementation.

Uses AsyncQdrantClient for non-blocking operations.
Supports dense vectors (Phase 1) and sparse vectors (Phase 2 ready).

Verified against qdrant-client==1.13.3.
"""

import asyncio
import logging
from typing import Any

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from callobuzz_rag.core.config import Settings
from callobuzz_rag.vectordb.base import (
    SearchResult,
    SparseVector,
    VectorPoint,
    VectorStorageProvider,
)

logger = logging.getLogger(__name__)


# Tries per search before a transport error is raised (see `_query_points`).
_SEARCH_ATTEMPTS = 3

# The keyword (BM25) leg. Chunks store BM25 term weights (retrieval/sparse.py); the
# IDF modifier makes Qdrant weight each query term by how rare it is across the
# collection at search time. Without it, words that occur in every document
# ("gear", "policy", the company name) score as highly as the distinctive ones,
# and on paraphrased questions the keyword leg returns noise that reciprocal
# rank fusion then promotes above dense search's correct answer.
_SPARSE_CONFIG = {"sparse_text": models.SparseVectorParams(modifier=models.Modifier.IDF)}


class QdrantVectorProvider(VectorStorageProvider):
    """Qdrant-based vector storage."""

    def __init__(self, settings: Settings) -> None:
        self._client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key_or_none,
        )
        self._default_collection = settings.qdrant_collection

    async def create_collection(self, name: str, vector_size: int) -> None:
        """Create collection with dense vector config. Idempotent.

        Args:
            name: Collection name.
            vector_size: Must match embedding dimensions (e.g., 1024 for Jina v3).
        """
        if vector_size <= 0:
            raise ValueError(f"vector_size must be positive, got {vector_size}")

        try:
            await self._client.create_collection(
                collection_name=name,
                vectors_config={
                    "text": models.VectorParams(
                        size=vector_size,
                        distance=models.Distance.COSINE,
                    ),
                },
                sparse_vectors_config=_SPARSE_CONFIG,
            )
            logger.info("Created Qdrant collection: %s", name)
        except UnexpectedResponse as e:
            if "already exists" in str(e):
                logger.info("Collection %s already exists", name)
                await self._ensure_sparse_idf(name)
            else:
                raise

    async def _ensure_sparse_idf(self, name: str) -> None:
        """Upgrade a collection created before IDF weighting, in place.

        Only the query-time modifier changes: stored sparse vectors are the same
        term frequencies either way, so no re-ingestion is needed. Failure is
        logged, not raised — the keyword leg still works, just without IDF.
        """
        try:
            await self._client.update_collection(
                collection_name=name, sparse_vectors_config=_SPARSE_CONFIG
            )
        except Exception:
            logger.warning("Could not enable IDF on sparse vectors of %s", name, exc_info=True)

    async def delete_collection(self, name: str) -> None:
        """Delete a Qdrant collection. No-op if collection does not exist."""
        try:
            await self._client.delete_collection(collection_name=name)
            logger.info("Deleted Qdrant collection: %s", name)
        except UnexpectedResponse as e:
            if "not found" in str(e).lower() or e.status_code == 404:
                logger.info("Collection %s does not exist, nothing to delete", name)
            else:
                raise

    async def upsert(self, collection: str, points: list[VectorPoint]) -> None:
        """Upsert vectors with payload. Includes sparse vector if present.

        An empty list is a no-op: Qdrant answers an empty update with a 400 ("Empty update
        request"), and a document with no chunks has nothing to write.
        """
        if not points:
            return
        qdrant_points: list[models.PointStruct] = []
        for point in points:
            vector: dict[str, Any] = {"text": point.vector}
            if point.sparse_vector is not None:
                vector["sparse_text"] = models.SparseVector(
                    indices=point.sparse_vector.indices,
                    values=point.sparse_vector.values,
                )
            qdrant_points.append(
                models.PointStruct(
                    id=point.id,
                    vector=vector,
                    payload=point.payload,
                )
            )
        await self._client.upsert(
            collection_name=collection,
            points=qdrant_points,
        )

    async def _query_points(self, **kwargs: Any) -> Any:
        """`query_points`, retried on transport failures.

        A pooled keep-alive connection that Qdrant (or anything between) has closed
        surfaces as a read error on the NEXT request that reuses it. Searches are
        idempotent reads, so retrying on a fresh connection is safe — and without it,
        one stale connection turned into a user-visible 502 under concurrent load.
        HTTP-level errors (a bad filter, a missing collection) are not retried.
        """
        for attempt in range(_SEARCH_ATTEMPTS):
            try:
                return await self._client.query_points(**kwargs)
            except ResponseHandlingException:
                if attempt == _SEARCH_ATTEMPTS - 1:
                    raise
                logger.warning("Qdrant transport error; retrying search (%d)", attempt + 1)
                await asyncio.sleep(0.05 * (attempt + 1))
        raise AssertionError("unreachable")

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Dense vector search with optional payload filtering."""
        query_filter = self._build_filter(filters) if filters else None

        results = await self._query_points(
            collection_name=collection,
            query=query_vector,
            using="text",
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
        )

        return [
            SearchResult(
                id=str(point.id),
                score=point.score if point.score is not None else 0.0,
                payload=dict(point.payload) if point.payload else {},
            )
            for point in results.points
        ]

    async def search_sparse(
        self,
        collection: str,
        sparse_vector: SparseVector,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Sparse vector search (BM25-style keyword matching)."""
        query_filter = self._build_filter(filters) if filters else None

        qdrant_sparse = models.SparseVector(
            indices=sparse_vector.indices,
            values=sparse_vector.values,
        )

        results = await self._query_points(
            collection_name=collection,
            query=qdrant_sparse,
            using="sparse_text",
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
        )

        return [
            SearchResult(
                id=str(point.id),
                score=point.score if point.score is not None else 0.0,
                payload=dict(point.payload) if point.payload else {},
            )
            for point in results.points
        ]

    async def update_vectors(self, collection: str, points: list[VectorPoint]) -> None:
        """Replace vectors in place; payloads are untouched."""
        if not points:
            return
        updates: list[models.PointVectors] = []
        for point in points:
            # Always written: a point whose new text has no keywords must not keep
            # the old text's keyword vector, so None clears it.
            sparse = point.sparse_vector
            vector: dict[str, Any] = {
                "sparse_text": models.SparseVector(
                    indices=sparse.indices if sparse else [],
                    values=sparse.values if sparse else [],
                ),
            }
            if point.vector:  # empty = keep the dense vector as it is
                vector["text"] = point.vector
            updates.append(models.PointVectors(id=point.id, vector=vector))
        await self._client.update_vectors(collection_name=collection, points=updates)

    async def delete(self, collection: str, ids: list[str]) -> None:
        """Delete points by ID."""
        await self._client.delete(
            collection_name=collection,
            points_selector=models.PointIdsList(points=ids),
        )

    async def delete_by_filter(self, collection: str, filters: dict[str, Any]) -> None:
        """Delete points matching filter criteria."""
        query_filter = self._build_filter(filters)
        await self._client.delete(
            collection_name=collection,
            points_selector=models.FilterSelector(filter=query_filter),
        )

    async def update_payload(
        self, collection: str, filters: dict[str, Any], payload: dict[str, Any]
    ) -> None:
        """Update payload fields for points matching filter criteria."""
        query_filter = self._build_filter(filters)
        await self._client.set_payload(
            collection_name=collection,
            payload=payload,
            points=models.FilterSelector(filter=query_filter),
        )

    async def set_payloads(self, collection: str, payloads: dict[str, dict[str, Any]]) -> None:
        """Set payload fields on individual points in one request (selected by point id, so
        no payload scan)."""
        if not payloads:
            return
        await self._client.batch_update_points(
            collection_name=collection,
            update_operations=[
                models.SetPayloadOperation(
                    set_payload=models.SetPayload(payload=payload, points=[point_id])
                )
                for point_id, payload in payloads.items()
            ],
        )

    async def health_check(self) -> bool:
        """Check if Qdrant is reachable."""
        try:
            await self._client.get_collections()
        except Exception:
            return False
        return True

    async def close(self) -> None:
        """Close the client connection."""
        await self._client.close()

    def _build_filter(self, filters: dict[str, Any]) -> models.Filter:
        """Convert simple dict filters to Qdrant Filter model.

        Supports: {"key": "value"} for exact match,
                  {"key": ["val1", "val2"]} for MatchAny.
        """
        conditions: list[models.FieldCondition] = []
        for key, value in filters.items():
            if isinstance(value, list):
                conditions.append(models.FieldCondition(key=key, match=models.MatchAny(any=value)))
            else:
                conditions.append(
                    models.FieldCondition(key=key, match=models.MatchValue(value=value))
                )
        return models.Filter(must=conditions)
