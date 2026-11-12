"""Reciprocal Rank Fusion (RRF) for merging ranked result lists.

Merges dense and sparse search results into a single ranked list.
Also handles deduplication when multiple query variants produce
overlapping results.

Reference: Cormack, Clarke, Buettcher (2009) — "Reciprocal Rank Fusion
outperforms Condorcet and individual Rank Learning Methods"
"""

from abc import ABC, abstractmethod
from typing import Any

from callobuzz_rag.vectordb.base import SearchResult


def rrf_score(rank: int, k: int = 60) -> float:
    """Compute RRF score for a document at the given rank.

    Args:
        rank: 1-indexed rank position (1 = top result).
        k: Smoothing constant. Default 60 per the original RRF paper.

    Returns:
        RRF score contribution for this rank position.

    Raises:
        ValueError: If rank < 1 or k < 0.
    """
    if rank < 1:
        raise ValueError(f"Rank must be >= 1, got {rank}")
    if k < 0:
        raise ValueError(f"k must be >= 0, got {k}")
    return 1.0 / (k + rank)


def merge_rrf(
    dense_results: list[SearchResult],
    sparse_results: list[SearchResult],
    k: int = 60,
) -> list[SearchResult]:
    """Merge two ranked result lists using Reciprocal Rank Fusion.

    Each item's fused score is the sum of its RRF contributions from each
    list it appears in. Items appearing in both lists get higher scores.

    Args:
        dense_results: Ranked results from dense (vector) search.
        sparse_results: Ranked results from sparse (keyword/BM25) search.
        k: RRF smoothing constant. Higher = more uniform weighting.

    Returns:
        Merged list sorted by fused RRF score (descending).
    """
    if not dense_results and not sparse_results:
        return []

    scores: dict[str, float] = {}
    payloads: dict[str, dict[str, Any]] = {}

    for rank, result in enumerate(dense_results, 1):
        scores[result.id] = scores.get(result.id, 0.0) + rrf_score(rank, k)
        payloads[result.id] = result.payload

    for rank, result in enumerate(sparse_results, 1):
        scores[result.id] = scores.get(result.id, 0.0) + rrf_score(rank, k)
        payloads.setdefault(result.id, result.payload)

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [
        SearchResult(id=doc_id, score=score, payload=payloads[doc_id]) for doc_id, score in merged
    ]


def merge_rrf_multi(
    result_lists: list[list[SearchResult]],
    k: int = 60,
) -> list[SearchResult]:
    """Merge N ranked result lists using RRF.

    Generalization of merge_rrf for multiple query variants. Each list
    contributes RRF scores independently, and items accumulate across
    all lists they appear in.

    Args:
        result_lists: List of ranked result lists (e.g., from different
            query rephrases each producing dense+sparse merged results).
        k: RRF smoothing constant.

    Returns:
        Merged and deduplicated list sorted by fused RRF score (descending).
    """
    if not result_lists:
        return []

    scores: dict[str, float] = {}
    payloads: dict[str, dict[str, Any]] = {}

    for results in result_lists:
        for rank, result in enumerate(results, 1):
            scores[result.id] = scores.get(result.id, 0.0) + rrf_score(rank, k)
            payloads.setdefault(result.id, result.payload)

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [
        SearchResult(id=doc_id, score=score, payload=payloads[doc_id]) for doc_id, score in merged
    ]


def _min_max(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    lo, hi = min(scores.values()), max(scores.values())
    if hi <= lo:
        return dict.fromkeys(scores, 1.0)
    return {doc: (s - lo) / (hi - lo) for doc, s in scores.items()}


def fuse_convex(
    dense_scores: dict[str, float],
    sparse_results: list[SearchResult],
    payloads: dict[str, dict[str, Any]],
    dense_weight: float = 0.75,
) -> list[SearchResult]:
    """`dense_weight * dense + (1 - dense_weight) * keyword`, each leg min-max
    normalized within the query; a chunk one leg did not return gets 0 from it.

    Chosen over RRF by `benchmarks/fusion_study.py`: tuned on BEIR dev/train qrels,
    it lifted mean test nDCG@10 over six datasets from 0.528 (dense alone) to
    0.537, where equal-weight RRF (k=60) LOWERED it to 0.505. RRF throws the scores
    away and treats a weak keyword match at rank 3 like a strong dense one; the
    keyword leg is the weaker of the two on natural-language questions, and
    rank-only fusion lets it pull good dense results down.
    """
    dense = _min_max(dense_scores)
    sparse = _min_max({r.id: r.score for r in sparse_results})
    for result in sparse_results:
        payloads.setdefault(result.id, result.payload)
    fused = {
        doc: dense_weight * dense.get(doc, 0.0) + (1 - dense_weight) * sparse.get(doc, 0.0)
        for doc in dense.keys() | sparse.keys()
    }
    ranked = sorted(fused.items(), key=lambda x: (-x[1], x[0]))
    return [SearchResult(id=doc, score=score, payload=payloads[doc]) for doc, score in ranked]


def deduplicate_results(results: list[SearchResult]) -> list[SearchResult]:
    """Remove duplicate results by ID, keeping the highest-scoring occurrence.

    Used when the same chunk appears in results from multiple query variants.

    Args:
        results: Potentially duplicated results, already sorted by score.

    Returns:
        Deduplicated list preserving the highest score for each unique ID.
    """
    seen: set[str] = set()
    deduplicated: list[SearchResult] = []

    for result in results:
        if result.id not in seen:
            seen.add(result.id)
            deduplicated.append(result)

    return deduplicated


# ---------------------------------------------------------------------------
# Pluggable fusion strategies (Epic 7-01)
# ---------------------------------------------------------------------------


class FusionStrategy(ABC):
    """Abstract base for fusion strategies."""

    @abstractmethod
    def fuse(self, result_lists: list[list[SearchResult]]) -> list[SearchResult]:
        """Fuse N ranked result lists into a single ranked list."""


class RRFFusionStrategy(FusionStrategy):
    """Reciprocal Rank Fusion — rank-based, score-normalization-free."""

    def __init__(self, k: int = 60) -> None:
        self._k = k

    def fuse(self, result_lists: list[list[SearchResult]]) -> list[SearchResult]:
        return merge_rrf_multi(result_lists, k=self._k)


class ScoreBasedFusionStrategy(FusionStrategy):
    """Weighted score fusion — applies per-list weights to raw scores."""

    def __init__(self, weights: list[float] | None = None) -> None:
        self._weights = weights

    def fuse(self, result_lists: list[list[SearchResult]]) -> list[SearchResult]:
        if not result_lists:
            return []

        n = len(result_lists)
        weights = self._weights or [1.0 / n] * n
        if len(weights) < n:
            weights = weights + [1.0 / n] * (n - len(weights))

        scores: dict[str, float] = {}
        payloads: dict[str, dict[str, Any]] = {}

        for i, results in enumerate(result_lists):
            w = weights[i] if i < len(weights) else weights[-1]
            for result in results:
                scores[result.id] = scores.get(result.id, 0.0) + result.score * w
                payloads.setdefault(result.id, result.payload)

        merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [
            SearchResult(id=doc_id, score=score, payload=payloads[doc_id])
            for doc_id, score in merged
        ]


class ConvexFusionStrategy(FusionStrategy):
    """Normalized weighted score fusion of [dense, keyword] — see `fuse_convex`."""

    def __init__(self, dense_weight: float = 0.75) -> None:
        self._dense_weight = dense_weight

    def fuse(self, result_lists: list[list[SearchResult]]) -> list[SearchResult]:
        if not result_lists:
            return []
        dense, *rest = result_lists
        sparse = rest[0] if rest else []
        payloads = {r.id: r.payload for r in dense}
        return fuse_convex(
            {r.id: r.score for r in dense}, sparse, payloads, dense_weight=self._dense_weight
        )


def create_fusion_strategy(
    strategy: str = "convex",
    rrf_k: int = 60,
    weights: list[float] | None = None,
    dense_weight: float = 0.75,
) -> FusionStrategy:
    """Factory function for fusion strategies."""
    if strategy == "convex":
        return ConvexFusionStrategy(dense_weight=dense_weight)
    if strategy == "rrf":
        return RRFFusionStrategy(k=rrf_k)
    if strategy == "score_based":
        return ScoreBasedFusionStrategy(weights=weights)
    raise ValueError(
        f"Unknown fusion strategy: {strategy!r}. Use 'convex', 'rrf' or 'score_based'."
    )
