"""Abstract base class for vector storage providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SparseVector:
    """Sparse vector with integer indices and float values."""

    indices: list[int]
    values: list[float]


@dataclass
class VectorPoint:
    """A point to upsert into the vector store."""

    id: str
    vector: list[float]
    payload: dict[str, Any] = field(default_factory=dict)
    sparse_vector: SparseVector | None = None


@dataclass
class SearchResult:
    """A single search result."""

    id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChunkPayload:
    """Canonical payload schema for Qdrant vectors.

    This is the single source of truth. Epic 10 orchestrator builds this,
    Epic 09 Qdrant provider stores it, Phase 2 search uses it for filtering.
    """

    chunk_id: str
    corpus_id: str
    document_id: str
    document_version_id: str
    page: int | None = None
    is_table: bool = False
    is_code: bool = False
    language: str | None = None
    keywords: list[str] = field(default_factory=list)
    category: str | None = None
    user_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Hash of the chunk's own text (`pipeline/text_hash.py`); equal values are the same
    # passage stored in more than one document, and search shows it once.
    text_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "chunk_id": self.chunk_id,
            "corpus_id": self.corpus_id,
            "document_id": self.document_id,
            "document_version_id": self.document_version_id,
            "page": self.page,
            "is_table": self.is_table,
            "is_code": self.is_code,
            "language": self.language,
            "keywords": self.keywords,
        }
        if self.category is not None:
            d["category"] = self.category
        if self.user_id is not None:
            d["user_id"] = self.user_id
        if self.text_hash is not None:
            d["text_hash"] = self.text_hash
        for key, value in self.metadata.items():
            d[f"meta_{key}"] = value
        return d


class VectorStorageProvider(ABC):
    """Interface for vector storage (Qdrant, pgvector, Milvus, etc.)."""

    @abstractmethod
    async def create_collection(self, name: str, vector_size: int) -> None:
        """Create a collection/index. Idempotent."""

    @abstractmethod
    async def delete_collection(self, name: str) -> None:
        """Delete a collection. No-op if collection does not exist."""

    @abstractmethod
    async def upsert(self, collection: str, points: list[VectorPoint]) -> None:
        """Insert or update vectors (dense + optional sparse)."""

    @abstractmethod
    async def search(
        self,
        collection: str,
        query_vector: list[float],
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Search by dense vector similarity."""

    @abstractmethod
    async def search_sparse(
        self,
        collection: str,
        sparse_vector: SparseVector,
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Search by sparse vector similarity (keyword/BM25-style)."""

    @abstractmethod
    async def delete(self, collection: str, ids: list[str]) -> None:
        """Delete points by ID."""

    @abstractmethod
    async def delete_by_filter(self, collection: str, filters: dict[str, Any]) -> None:
        """Delete points matching filter criteria."""

    @abstractmethod
    async def update_vectors(self, collection: str, points: list[VectorPoint]) -> None:
        """Replace existing points' dense and sparse vectors in place.

        A point with `sparse_vector=None` has its keyword vector cleared; one with
        an empty `vector` keeps its dense vector. The payload is left untouched — `VectorPoint.payload` is ignored — so a
        re-embed never loses the fields that version and deletion filters rely on.
        """

    @abstractmethod
    async def update_payload(
        self, collection: str, filters: dict[str, Any], payload: dict[str, Any]
    ) -> None:
        """Update payload fields for points matching filter criteria."""

    async def set_payloads(self, collection: str, payloads: dict[str, dict[str, Any]]) -> None:
        """Set payload fields on individual points: {point id: {field: value}}.

        The default goes through `update_payload`, one filter per point (the point id is
        also the payload's `chunk_id`). A store with a cheaper way overrides it.
        """
        for point_id, payload in payloads.items():
            await self.update_payload(collection, {"chunk_id": point_id}, payload)

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if vector store is reachable."""
