"""Tests for pluggable fusion strategies."""

import pytest

from callobuzz_rag.vectordb.base import SearchResult


def _make_result(doc_id: str, score: float) -> SearchResult:
    return SearchResult(id=doc_id, score=score, payload={"chunk_id": doc_id})


class TestRRFFusionStrategy:
    def test_fuse_two_lists(self):
        """RRF should merge two ranked lists by rank position."""
        from callobuzz_rag.retrieval.fusion import RRFFusionStrategy

        strategy = RRFFusionStrategy(k=60)
        dense = [_make_result("a", 0.9), _make_result("b", 0.8)]
        sparse = [_make_result("b", 0.7), _make_result("c", 0.6)]
        merged = strategy.fuse([dense, sparse])

        # "b" appears in both lists -> highest score
        assert merged[0].id == "b"
        assert len(merged) == 3

    def test_fuse_empty_lists(self):
        from callobuzz_rag.retrieval.fusion import RRFFusionStrategy

        strategy = RRFFusionStrategy(k=60)
        assert strategy.fuse([]) == []
        assert strategy.fuse([[], []]) == []

    def test_fuse_single_list(self):
        from callobuzz_rag.retrieval.fusion import RRFFusionStrategy

        strategy = RRFFusionStrategy(k=60)
        results = [_make_result("a", 0.9), _make_result("b", 0.8)]
        merged = strategy.fuse([results])
        assert len(merged) == 2
        assert merged[0].id == "a"

    def test_custom_k_value(self):
        from callobuzz_rag.retrieval.fusion import RRFFusionStrategy

        strategy = RRFFusionStrategy(k=1)
        dense = [_make_result("a", 0.9)]
        sparse = [_make_result("a", 0.7)]
        merged = strategy.fuse([dense, sparse])
        assert len(merged) == 1
        # k=1: score = 1/(1+1) + 1/(1+1) = 1.0
        assert merged[0].score == pytest.approx(1.0)


class TestScoreBasedFusionStrategy:
    def test_fuse_with_weights(self):
        """Score-based fusion should apply weights to each list."""
        from callobuzz_rag.retrieval.fusion import ScoreBasedFusionStrategy

        strategy = ScoreBasedFusionStrategy(weights=[0.7, 0.3])
        dense = [_make_result("a", 1.0), _make_result("b", 0.5)]
        sparse = [_make_result("b", 1.0), _make_result("c", 0.8)]
        merged = strategy.fuse([dense, sparse])

        # "a" appears in dense only: 1.0*0.7 = 0.7
        # "b" appears in both: 0.5*0.7 + 1.0*0.3 = 0.65
        # "c" appears in sparse only: 0.8*0.3 = 0.24
        assert merged[0].id == "a"  # 0.7
        assert merged[1].id == "b"  # 0.65

    def test_fuse_equal_weights_default(self):
        from callobuzz_rag.retrieval.fusion import ScoreBasedFusionStrategy

        strategy = ScoreBasedFusionStrategy()
        dense = [_make_result("a", 1.0)]
        sparse = [_make_result("a", 0.5)]
        merged = strategy.fuse([dense, sparse])
        # Equal weights (0.5, 0.5): 1.0*0.5 + 0.5*0.5 = 0.75
        assert merged[0].score == pytest.approx(0.75)


class TestCreateFusionStrategy:
    def test_rrf_is_default(self):
        from callobuzz_rag.retrieval.fusion import create_fusion_strategy

        strategy = create_fusion_strategy("rrf", rrf_k=60)
        assert type(strategy).__name__ == "RRFFusionStrategy"

    def test_score_based(self):
        from callobuzz_rag.retrieval.fusion import create_fusion_strategy

        strategy = create_fusion_strategy("score_based")
        assert type(strategy).__name__ == "ScoreBasedFusionStrategy"

    def test_invalid_strategy_raises(self):
        from callobuzz_rag.retrieval.fusion import create_fusion_strategy

        with pytest.raises(ValueError, match="Unknown fusion strategy"):
            create_fusion_strategy("invalid_strategy")


class TestHybridSearcherFusionIntegration:
    def test_searcher_accepts_fusion_strategy(self):
        """HybridSearcher should accept a custom FusionStrategy."""
        from unittest.mock import MagicMock

        from callobuzz_rag.retrieval.fusion import ScoreBasedFusionStrategy
        from callobuzz_rag.retrieval.search import HybridSearcher

        settings = MagicMock()
        settings.retrieval_top_k_dense = 20
        settings.retrieval_top_k_sparse = 20
        settings.rrf_k = 60
        settings.fusion_strategy = "rrf"
        settings.qdrant_collection = "test"

        strategy = ScoreBasedFusionStrategy(weights=[0.6, 0.4])
        searcher = HybridSearcher(
            vector_store=MagicMock(),
            embedding_provider=MagicMock(),
            embedding_cache=MagicMock(),
            session=MagicMock(),
            settings=settings,
            fusion_strategy=strategy,
        )
        assert searcher._fusion_strategy is strategy
