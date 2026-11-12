"""Data models for the retrieval subsystem."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class QueryComplexity(StrEnum):
    """Query complexity classification.

    Determines retrieval strategy: simple queries get single-pass retrieval,
    complex queries get sub-question decomposition and broader search.
    """

    SIMPLE = "simple"
    MULTI_STEP = "multi_step"
    COMPARISON = "comparison"
    ANALYTICAL = "analytical"


@dataclass
class QueryPlan:
    """Query execution plan produced by the understanding layer.

    The retrieval orchestrator uses this plan to decide how many search
    passes to run, whether to use HyDE, and how to merge results.
    """

    complexity: QueryComplexity
    original_query: str
    sub_queries: list[str] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)
    hyde_text: str | None = None
    filters: dict[str, Any] = field(default_factory=dict)

    @property
    def all_queries(self) -> list[str]:
        """All queries to execute: original + sub-queries + variants.

        Deduplicates and returns a flat list for the retrieval layer.
        """
        seen: set[str] = set()
        result: list[str] = []

        for q in [self.original_query, *self.sub_queries, *self.variants]:
            normalized = q.strip().lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(q.strip())

        return result

    @property
    def needs_decomposition(self) -> bool:
        """Whether this query type benefits from sub-question decomposition."""
        return self.complexity in (
            QueryComplexity.MULTI_STEP,
            QueryComplexity.COMPARISON,
            QueryComplexity.ANALYTICAL,
        )


@dataclass
class PreprocessedQuery:
    """Result of query preprocessing.

    Contains the original query, detected language, and extracted keywords
    after stop-word removal and language-specific tokenization.
    """

    original: str
    normalized: str
    language: str
    keywords: list[str] = field(default_factory=list)
    is_cjk: bool = False

    @property
    def keyword_text(self) -> str:
        """Reconstruct keyword-only text for sparse vector generation."""
        return " ".join(self.keywords)

    @property
    def is_empty(self) -> bool:
        """True if preprocessing removed all meaningful content."""
        return len(self.keywords) == 0


@dataclass
class RetrievalResult:
    """A single retrieved chunk with its score and metadata."""

    chunk_id: str
    text: str
    score: float
    corpus_id: str
    document_id: str
    document_version_id: str
    page: int | None = None
    is_table: bool = False
    is_code: bool = False
    language: str | None = None
    keywords: list[str] = field(default_factory=list)
    has_parent_context: bool = False
    dense_rank: int | None = None
    sparse_rank: int | None = None
    # Other documents that hold this same passage (collapsed into this result), at most 5:
    # [{"document_id": ..., "file_name": ...}].
    also_in: list[dict[str, str | None]] = field(default_factory=list)


@dataclass
class ChunkScoreBreakdown:
    """Why a chunk ranked where it did.

    The pipeline collapses everything it knows into one number — the RRF score —
    and the stages that run after fusion (rerank, MMR, decay) only REORDER; they
    never rewrite it. So the single `score` a caller sees cannot say whether a
    chunk arrived by dense similarity, by an exact keyword hit, or because the
    cross-encoder rescued it from rank 40. This carries each stage's own number
    out to the caller, so it can log which stage is actually earning its latency
    instead of recomputing a proxy from the text it got back.

    `None` means the stage did not run for this query, which is itself the answer
    to "why didn't hybrid help" — it was off.
    """

    fused_score: float
    pipeline_rank: int
    category: str | None = None
    category_weight: float | None = None
    dense_rank: int | None = None
    sparse_rank: int | None = None
    dense_score: float | None = None
    sparse_score: float | None = None
    rerank_score: float | None = None
    decay_multiplier: float | None = None
    age_days: float | None = None


@dataclass
class SearchFilters:
    """Filters to apply during hybrid search."""

    corpus_id: str
    language: str | None = None
    is_table: bool | None = None
    is_code: bool | None = None
    document_id: str | None = None
    category: str | None = None
    user_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_qdrant_filters(self) -> dict[str, Any]:
        """Convert to dict format expected by VectorStorageProvider.search()."""
        filters: dict[str, Any] = {
            "corpus_id": self.corpus_id,
        }
        if self.language is not None:
            filters["language"] = self.language
        if self.is_table is not None:
            filters["is_table"] = self.is_table
        if self.is_code is not None:
            filters["is_code"] = self.is_code
        if self.document_id is not None:
            filters["document_id"] = self.document_id
        if self.category is not None:
            filters["category"] = self.category
        if self.user_id is not None:
            filters["user_id"] = self.user_id
        for key, value in self.metadata.items():
            filters[f"meta_{key}"] = value
        return filters
