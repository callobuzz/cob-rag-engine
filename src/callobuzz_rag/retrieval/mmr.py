"""Maximal Marginal Relevance (MMR) diversity re-ranking.

Iteratively selects results that balance relevance with diversity,
preventing near-duplicate chunks from dominating the top results.

Algorithm ported from OpenClaw mining reference section 1.1.
Uses Jaccard similarity on tokenized text for diversity measurement.
"""

from dataclasses import dataclass

from callobuzz_rag.text.tokenize import word_set


@dataclass
class MMRItem:
    """An item to be processed by MMR re-ranking.

    Attributes:
        id: Unique identifier for the item.
        score: Relevance score (from cross-encoder or RRF).
        content: Text content used for similarity comparison.
    """

    id: str
    score: float
    content: str


def tokenize(text: str) -> set[str]:
    """Tokenize text into a set of lowercase word tokens.

    Words are letters, combining marks and digits (`text/tokenize.py`), so Hindi and other
    Indic words stay whole; the old `[a-z0-9_]+` gave every such text an empty set.

    Args:
        text: Input text to tokenize.

    Returns:
        Set of unique lowercase tokens.
    """
    return word_set(text)


def jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Compute Jaccard similarity between two token sets.

    Jaccard(A, B) = |A intersect B| / |A union B|

    Returns:
        Jaccard similarity in [0.0, 1.0].
        Returns 1.0 if both sets are empty (identical empty documents).
        Returns 0.0 if exactly one set is empty.
    """
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


def mmr_rerank(
    items: list[MMRItem],
    lambda_: float = 0.7,
) -> list[MMRItem]:
    """Re-rank items using Maximal Marginal Relevance.

    Iteratively selects the item that maximizes:
        MMR(d) = lambda * relevance(d) - (1 - lambda) * max_sim(d, selected)

    Args:
        items: Items to re-rank. Each must have id, score, and content.
        lambda_: Trade-off between relevance (1.0) and diversity (0.0).
            Clamped to [0.0, 1.0]. Default 0.7 per OpenClaw.

    Returns:
        Items re-ordered by MMR score. All items are returned (MMR
        changes order, not count).
    """
    if not items:
        return []

    # Clamp lambda to [0, 1]
    lambda_ = max(0.0, min(1.0, lambda_))

    # Lambda = 1.0 means pure relevance — no diversity penalty
    if len(items) <= 1 or lambda_ == 1.0:
        return sorted(items, key=lambda x: x.score, reverse=True)

    # Pre-tokenize all items (cache for efficiency)
    token_cache: dict[str, set[str]] = {item.id: tokenize(item.content) for item in items}

    # Normalize scores to [0, 1] via min-max
    max_score = max(item.score for item in items)
    min_score = min(item.score for item in items)
    score_range = max_score - min_score

    def normalize(score: float) -> float:
        if score_range == 0.0:
            return 1.0
        return (score - min_score) / score_range

    # Each candidate's highest similarity to anything selected so far, updated
    # with only the newest pick after every round: O(n^2) Jaccard calls instead of
    # O(n^3), with identical choices. Candidates are scanned in input order, so an
    # exact tie resolves the same way on every run.
    selected: list[MMRItem] = []
    remaining: list[MMRItem] = list(items)
    max_sim: dict[str, float] = {item.id: 0.0 for item in items}

    while remaining:
        best_index = -1
        best_mmr = float("-inf")
        for index, candidate in enumerate(remaining):
            mmr_score = (
                lambda_ * normalize(candidate.score) - (1.0 - lambda_) * max_sim[candidate.id]
            )
            # Tiebreaker: prefer higher original score
            if mmr_score > best_mmr or (
                mmr_score == best_mmr and candidate.score > remaining[best_index].score
            ):
                best_mmr = mmr_score
                best_index = index

        chosen = remaining.pop(best_index)
        selected.append(chosen)
        chosen_tokens = token_cache[chosen.id]
        for candidate in remaining:
            sim = jaccard_similarity(token_cache[candidate.id], chosen_tokens)
            if sim > max_sim[candidate.id]:
                max_sim[candidate.id] = sim

    return selected
