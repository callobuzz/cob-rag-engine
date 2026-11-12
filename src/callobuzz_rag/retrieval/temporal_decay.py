"""Temporal decay scoring for preferring recent documents.

Applies an exponential decay multiplier to relevance scores based on
document age. Score halves every `half_life_days` (default 30).

Algorithm ported from OpenClaw mining reference section 1.2.

Formula:
    decayed_score = score * e^(-lambda * age_days)
    where lambda = ln(2) / half_life_days
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class DecayResult:
    """Result of applying temporal decay to a scored item.

    Attributes:
        id: Unique identifier for the item.
        original_score: Score before decay was applied.
        decayed_score: Score after decay multiplier.
        multiplier: The decay multiplier that was applied.
        age_days: Age of the item in days.
    """

    id: str
    original_score: float
    decayed_score: float
    multiplier: float
    age_days: float


def to_decay_lambda(half_life_days: float) -> float:
    """Convert half-life in days to the exponential decay constant.

    Returns 0.0 for invalid inputs (zero, negative, infinite half-life),
    which disables decay.
    """
    if half_life_days <= 0 or not math.isfinite(half_life_days):
        return 0.0
    return math.log(2) / half_life_days


def temporal_decay_multiplier(
    age_days: float,
    half_life_days: float,
) -> float:
    """Compute the decay multiplier for a given age.

    Args:
        age_days: Age of the document in days. Negative values are
            clamped to 0 (future documents get no decay).
        half_life_days: Number of days for the score to halve.

    Returns:
        Multiplier in (0.0, 1.0]. Returns 1.0 if decay is disabled.
    """
    lam = to_decay_lambda(half_life_days)
    age = max(0.0, age_days)

    if lam <= 0 or not math.isfinite(age):
        return 1.0

    return math.exp(-lam * age)


def apply_temporal_decay(
    score: float,
    age_days: float,
    half_life_days: float = 30.0,
) -> float:
    """Apply temporal decay to a single score.

    Returns:
        Decayed score = score * decay_multiplier.
    """
    return score * temporal_decay_multiplier(age_days, half_life_days)


def compute_age_days(
    created_at: datetime,
    now: datetime | None = None,
) -> float:
    """Compute age in days from a creation timestamp.

    Args:
        created_at: When the document/chunk was created.
            Must be timezone-aware (UTC recommended).
        now: Current time. Defaults to UTC now if not provided.

    Returns:
        Age in fractional days. Clamped to >= 0.
    """
    if now is None:
        now = datetime.now(UTC)

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    delta = now - created_at
    age = delta.total_seconds() / 86400.0
    return max(0.0, age)


def apply_decay_to_results(
    items: list[tuple[str, float, datetime]],
    half_life_days: float = 30.0,
    now: datetime | None = None,
    evergreen_ids: set[str] | None = None,
) -> list[DecayResult]:
    """Apply temporal decay to a list of scored items.

    Items marked as evergreen (via evergreen_ids) receive a multiplier
    of 1.0 regardless of age.

    Args:
        items: List of (id, score, created_at) tuples.
        half_life_days: Number of days for the score to halve.
        now: Current time for age calculation. Defaults to UTC now.
        evergreen_ids: Set of item IDs that should not decay.

    Returns:
        List of DecayResult sorted by decayed_score descending.
    """
    if now is None:
        now = datetime.now(UTC)

    if evergreen_ids is None:
        evergreen_ids = set()

    results: list[DecayResult] = []

    for item_id, score, created_at in items:
        age = compute_age_days(created_at, now)

        if item_id in evergreen_ids:
            results.append(
                DecayResult(
                    id=item_id,
                    original_score=score,
                    decayed_score=score,
                    multiplier=1.0,
                    age_days=age,
                )
            )
            continue

        multiplier = temporal_decay_multiplier(age, half_life_days)
        decayed = score * multiplier

        results.append(
            DecayResult(
                id=item_id,
                original_score=score,
                decayed_score=decayed,
                multiplier=multiplier,
                age_days=age,
            )
        )

    results.sort(key=lambda r: r.decayed_score, reverse=True)
    return results
