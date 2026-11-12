"""Unit tests for RRF result fusion."""

import pytest

from callobuzz_rag.retrieval.fusion import (
    deduplicate_results,
    merge_rrf,
    merge_rrf_multi,
    rrf_score,
)
from callobuzz_rag.vectordb.base import SearchResult


class TestRRFScore:
    def test_rank_1_k_60(self) -> None:
        score = rrf_score(1, k=60)
        assert abs(score - 1 / 61) < 1e-9

    def test_rank_1_k_0(self) -> None:
        assert rrf_score(1, k=0) == 1.0

    def test_rank_2_k_60(self) -> None:
        score = rrf_score(2, k=60)
        assert abs(score - 1 / 62) < 1e-9

    def test_rank_0_raises(self) -> None:
        with pytest.raises(ValueError, match="Rank must be >= 1"):
            rrf_score(0)

    def test_negative_rank_raises(self) -> None:
        with pytest.raises(ValueError, match="Rank must be >= 1"):
            rrf_score(-1)

    def test_negative_k_raises(self) -> None:
        with pytest.raises(ValueError, match="k must be >= 0"):
            rrf_score(1, k=-1)


class TestMergeRRF:
    def test_empty_both(self) -> None:
        assert merge_rrf([], []) == []

    def test_dense_only(self) -> None:
        dense = [
            SearchResult(id="a", score=0.9, payload={"x": 1}),
            SearchResult(id="b", score=0.8, payload={"x": 2}),
        ]
        merged = merge_rrf(dense, [])
        assert len(merged) == 2
        assert merged[0].id == "a"
        assert abs(merged[0].score - 1 / 61) < 1e-9

    def test_sparse_only(self) -> None:
        sparse = [SearchResult(id="c", score=5.0, payload={})]
        merged = merge_rrf([], sparse)
        assert len(merged) == 1
        assert merged[0].id == "c"

    def test_overlap_boosts_score(self) -> None:
        dense = [SearchResult(id="a", score=0.9, payload={"from": "dense"})]
        sparse = [SearchResult(id="a", score=5.0, payload={"from": "sparse"})]
        merged = merge_rrf(dense, sparse)
        assert len(merged) == 1
        expected = 1 / 61 + 1 / 61
        assert abs(merged[0].score - expected) < 1e-9

    def test_dense_payload_preferred_for_overlap(self) -> None:
        dense = [SearchResult(id="a", score=0.9, payload={"source": "dense"})]
        sparse = [SearchResult(id="a", score=5.0, payload={"source": "sparse"})]
        merged = merge_rrf(dense, sparse)
        assert merged[0].payload["source"] == "dense"

    def test_sorted_descending(self) -> None:
        dense = [
            SearchResult(id="a", score=0.9, payload={}),
            SearchResult(id="b", score=0.7, payload={}),
        ]
        sparse = [
            SearchResult(id="b", score=5.0, payload={}),
            SearchResult(id="c", score=3.0, payload={}),
        ]
        merged = merge_rrf(dense, sparse)
        scores = [m.score for m in merged]
        assert scores == sorted(scores, reverse=True)

    def test_single_item(self) -> None:
        dense = [SearchResult(id="a", score=1.0, payload={})]
        merged = merge_rrf(dense, [])
        assert len(merged) == 1

    def test_custom_k(self) -> None:
        dense = [SearchResult(id="a", score=0.9, payload={})]
        merged = merge_rrf(dense, [], k=0)
        assert merged[0].score == 1.0


class TestMergeRRFMulti:
    def test_empty_lists(self) -> None:
        assert merge_rrf_multi([]) == []

    def test_single_list(self) -> None:
        results = [SearchResult(id="a", score=0.9, payload={})]
        merged = merge_rrf_multi([results])
        assert len(merged) == 1

    def test_three_lists_accumulate(self) -> None:
        list_a = [SearchResult(id="x", score=0.9, payload={})]
        list_b = [SearchResult(id="x", score=0.8, payload={})]
        list_c = [SearchResult(id="x", score=0.7, payload={})]
        merged = merge_rrf_multi([list_a, list_b, list_c])
        expected = 3 * (1 / 61)
        assert abs(merged[0].score - expected) < 1e-9

    def test_deduplication(self) -> None:
        list_a = [
            SearchResult(id="a", score=0.9, payload={}),
            SearchResult(id="b", score=0.8, payload={}),
        ]
        list_b = [
            SearchResult(id="a", score=0.7, payload={}),
            SearchResult(id="c", score=0.6, payload={}),
        ]
        merged = merge_rrf_multi([list_a, list_b])
        ids = [m.id for m in merged]
        assert len(ids) == 3
        assert len(set(ids)) == 3


class TestDeduplicateResults:
    def test_no_duplicates(self) -> None:
        results = [
            SearchResult(id="a", score=0.9, payload={}),
            SearchResult(id="b", score=0.8, payload={}),
        ]
        deduped = deduplicate_results(results)
        assert len(deduped) == 2

    def test_keeps_first_occurrence(self) -> None:
        results = [
            SearchResult(id="a", score=0.9, payload={}),
            SearchResult(id="a", score=0.5, payload={}),
        ]
        deduped = deduplicate_results(results)
        assert len(deduped) == 1
        assert deduped[0].score == 0.9

    def test_empty_list(self) -> None:
        assert deduplicate_results([]) == []

    def test_all_duplicates(self) -> None:
        results = [
            SearchResult(id="a", score=0.9, payload={}),
            SearchResult(id="a", score=0.8, payload={}),
            SearchResult(id="a", score=0.7, payload={}),
        ]
        deduped = deduplicate_results(results)
        assert len(deduped) == 1
