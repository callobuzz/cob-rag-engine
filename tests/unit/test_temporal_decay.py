"""Unit tests for temporal decay scoring."""

import math
from datetime import UTC, datetime

from callobuzz_rag.retrieval.temporal_decay import (
    apply_decay_to_results,
    apply_temporal_decay,
    compute_age_days,
    temporal_decay_multiplier,
    to_decay_lambda,
)


class TestToDecayLambda:
    def test_30_day_half_life(self) -> None:
        lam = to_decay_lambda(30.0)
        assert abs(lam - math.log(2) / 30) < 1e-9

    def test_zero_returns_zero(self) -> None:
        assert to_decay_lambda(0.0) == 0.0

    def test_negative_returns_zero(self) -> None:
        assert to_decay_lambda(-5.0) == 0.0

    def test_inf_returns_zero(self) -> None:
        assert to_decay_lambda(float("inf")) == 0.0


class TestTemporalDecayMultiplier:
    def test_zero_age_is_one(self) -> None:
        assert temporal_decay_multiplier(0, 30) == 1.0

    def test_one_half_life(self) -> None:
        mult = temporal_decay_multiplier(30, 30)
        assert abs(mult - 0.5) < 1e-6

    def test_two_half_lives(self) -> None:
        mult = temporal_decay_multiplier(60, 30)
        assert abs(mult - 0.25) < 1e-6

    def test_three_half_lives(self) -> None:
        mult = temporal_decay_multiplier(90, 30)
        assert abs(mult - 0.125) < 1e-6

    def test_seven_days(self) -> None:
        mult = temporal_decay_multiplier(7, 30)
        expected = math.exp(-math.log(2) / 30 * 7)
        assert abs(mult - expected) < 1e-6
        assert abs(mult - 0.851) < 0.001

    def test_negative_age_clamped(self) -> None:
        assert temporal_decay_multiplier(-5, 30) == 1.0

    def test_invalid_half_life_zero(self) -> None:
        assert temporal_decay_multiplier(30, 0) == 1.0

    def test_invalid_half_life_inf(self) -> None:
        assert temporal_decay_multiplier(30, float("inf")) == 1.0

    def test_inf_age_returns_one(self) -> None:
        assert temporal_decay_multiplier(float("inf"), 30) == 1.0


class TestApplyTemporalDecay:
    def test_basic(self) -> None:
        result = apply_temporal_decay(1.0, 7, 30)
        assert abs(result - 0.851) < 0.001

    def test_zero_score(self) -> None:
        assert apply_temporal_decay(0.0, 30, 30) == 0.0

    def test_zero_age(self) -> None:
        assert apply_temporal_decay(0.8, 0, 30) == 0.8


class TestComputeAgeDays:
    def test_basic(self) -> None:
        now = datetime(2025, 1, 31, tzinfo=UTC)
        created = datetime(2025, 1, 1, tzinfo=UTC)
        age = compute_age_days(created, now)
        assert abs(age - 30.0) < 1e-6

    def test_future_timestamp_clamped(self) -> None:
        now = datetime(2025, 1, 1, tzinfo=UTC)
        created = datetime(2025, 2, 1, tzinfo=UTC)
        age = compute_age_days(created, now)
        assert age == 0.0

    def test_naive_datetime_assumed_utc(self) -> None:
        now = datetime(2025, 1, 31, tzinfo=UTC)
        created = datetime(2025, 1, 1)  # naive
        age = compute_age_days(created, now)
        assert abs(age - 30.0) < 1e-6

    def test_fractional_days(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        created = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        age = compute_age_days(created, now)
        assert abs(age - 0.5) < 1e-6


class TestApplyDecayToResults:
    def test_empty_list(self) -> None:
        assert apply_decay_to_results([]) == []

    def test_sorted_by_decayed_score(self) -> None:
        now = datetime(2025, 2, 1, tzinfo=UTC)
        items = [
            ("old", 1.0, datetime(2024, 12, 1, tzinfo=UTC)),
            ("new", 0.8, datetime(2025, 1, 30, tzinfo=UTC)),
        ]
        results = apply_decay_to_results(items, half_life_days=30, now=now)
        assert results[0].id == "new"  # Recent doc wins despite lower score

    def test_evergreen_no_decay(self) -> None:
        now = datetime(2025, 2, 1, tzinfo=UTC)
        items = [
            ("evergreen_doc", 1.0, datetime(2024, 1, 1, tzinfo=UTC)),
        ]
        results = apply_decay_to_results(
            items,
            half_life_days=30,
            now=now,
            evergreen_ids={"evergreen_doc"},
        )
        assert results[0].multiplier == 1.0
        assert results[0].decayed_score == 1.0

    def test_decay_result_fields(self) -> None:
        now = datetime(2025, 2, 1, tzinfo=UTC)
        items = [
            ("a", 1.0, datetime(2025, 1, 2, tzinfo=UTC)),
        ]
        results = apply_decay_to_results(items, half_life_days=30, now=now)
        r = results[0]
        assert r.id == "a"
        assert r.original_score == 1.0
        assert r.decayed_score < 1.0
        assert r.multiplier < 1.0
        assert r.age_days > 0

    def test_same_age_ordered_by_original_score(self) -> None:
        now = datetime(2025, 2, 1, tzinfo=UTC)
        created = datetime(2025, 1, 15, tzinfo=UTC)
        items = [
            ("low", 0.5, created),
            ("high", 0.9, created),
        ]
        results = apply_decay_to_results(items, half_life_days=30, now=now)
        assert results[0].id == "high"

    def test_mixed_evergreen_and_decaying(self) -> None:
        now = datetime(2025, 3, 1, tzinfo=UTC)
        items = [
            ("old_but_green", 0.8, datetime(2024, 1, 1, tzinfo=UTC)),
            ("new_normal", 0.9, datetime(2025, 2, 28, tzinfo=UTC)),
            ("old_normal", 0.95, datetime(2024, 1, 1, tzinfo=UTC)),
        ]
        results = apply_decay_to_results(
            items,
            half_life_days=30,
            now=now,
            evergreen_ids={"old_but_green"},
        )
        # new_normal: 0.9 * ~1.0 (1 day old)
        # old_but_green: 0.8 * 1.0 (evergreen)
        # old_normal: 0.95 * very_small (14+ months old)
        assert results[0].id == "new_normal"
        assert results[1].id == "old_but_green"
        assert results[2].id == "old_normal"
