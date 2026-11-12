"""Unit tests for hybrid retrieval search filters and RRF integration."""

from callobuzz_rag.retrieval.fusion import merge_rrf
from callobuzz_rag.retrieval.models import RetrievalResult, SearchFilters
from callobuzz_rag.vectordb.base import SearchResult


class TestSearchFilters:
    def test_required_corpus_id(self) -> None:
        filters = SearchFilters(corpus_id="test-corpus")
        result = filters.to_qdrant_filters()
        assert result["corpus_id"] == "test-corpus"

    def test_optional_filters(self) -> None:
        filters = SearchFilters(
            corpus_id="test-corpus",
            language="en",
            is_table=False,
            document_id="doc-123",
        )
        result = filters.to_qdrant_filters()
        assert result["corpus_id"] == "test-corpus"
        assert result["language"] == "en"
        assert result["is_table"] is False
        assert result["document_id"] == "doc-123"

    def test_none_filters_excluded(self) -> None:
        filters = SearchFilters(corpus_id="test-corpus")
        result = filters.to_qdrant_filters()
        assert "language" not in result
        assert "is_table" not in result
        assert "is_code" not in result
        assert "document_id" not in result

    def test_is_code_filter(self) -> None:
        filters = SearchFilters(corpus_id="c", is_code=True)
        result = filters.to_qdrant_filters()
        assert result["is_code"] is True


class TestRRFMergeIntegration:
    """Tests RRF merge behavior as used by HybridSearcher."""

    def test_single_list_dense(self) -> None:
        dense = [
            SearchResult(id="1", score=0.9, payload={"chunk_id": "a"}),
            SearchResult(id="2", score=0.8, payload={"chunk_id": "b"}),
        ]
        merged = merge_rrf(dense, [])
        assert len(merged) == 2
        assert merged[0].id == "1"

    def test_overlapping_results_boost(self) -> None:
        dense = [
            SearchResult(id="1", score=0.9, payload={"chunk_id": "a"}),
            SearchResult(id="2", score=0.8, payload={"chunk_id": "b"}),
        ]
        sparse = [
            SearchResult(id="1", score=5.0, payload={"chunk_id": "a"}),
            SearchResult(id="3", score=3.0, payload={"chunk_id": "c"}),
        ]
        merged = merge_rrf(dense, sparse)
        scores = {m.id: m.score for m in merged}
        assert scores["1"] > scores["2"]
        assert scores["1"] > scores["3"]

    def test_rrf_score_formula(self) -> None:
        dense = [SearchResult(id="1", score=0.9, payload={"chunk_id": "a"})]
        sparse = [SearchResult(id="1", score=5.0, payload={"chunk_id": "a"})]
        merged = merge_rrf(dense, sparse)
        expected = 1.0 / (60 + 1) + 1.0 / (60 + 1)
        assert abs(merged[0].score - expected) < 1e-9

    def test_empty_both(self) -> None:
        assert merge_rrf([], []) == []

    def test_sorted_by_score_descending(self) -> None:
        dense = [
            SearchResult(id="1", score=0.9, payload={"chunk_id": "a"}),
            SearchResult(id="2", score=0.7, payload={"chunk_id": "b"}),
            SearchResult(id="3", score=0.5, payload={"chunk_id": "c"}),
        ]
        sparse = [
            SearchResult(id="3", score=5.0, payload={"chunk_id": "c"}),
            SearchResult(id="1", score=3.0, payload={"chunk_id": "a"}),
        ]
        merged = merge_rrf(dense, sparse)
        scores = [m.score for m in merged]
        assert scores == sorted(scores, reverse=True)


class TestRankTracking:
    """Tests that dense_rank and sparse_rank are set on RetrievalResult."""

    def test_rank_values_populated(self) -> None:
        """RetrievalResult stores dense and sparse rank positions."""
        result = RetrievalResult(
            chunk_id="abc",
            text="test",
            score=0.5,
            corpus_id="corp",
            document_id="doc",
            document_version_id="dv",
            dense_rank=1,
            sparse_rank=3,
        )
        assert result.dense_rank == 1
        assert result.sparse_rank == 3

    def test_rank_defaults_none(self) -> None:
        """Ranks default to None when not set."""
        result = RetrievalResult(
            chunk_id="abc",
            text="test",
            score=0.5,
            corpus_id="corp",
            document_id="doc",
            document_version_id="dv",
        )
        assert result.dense_rank is None
        assert result.sparse_rank is None
