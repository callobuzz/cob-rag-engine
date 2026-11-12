"""Convex (normalized, weighted) fusion of the dense and keyword legs."""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from callobuzz_rag.retrieval.fusion import ConvexFusionStrategy, create_fusion_strategy, fuse_convex
from callobuzz_rag.vectordb.base import SearchResult


def _r(doc: str, score: float) -> SearchResult:
    return SearchResult(id=doc, score=score, payload={"doc": doc})


class TestFuseConvex:
    def test_weights_normalized_legs(self) -> None:
        dense = {"a": 0.90, "b": 0.80, "c": 0.70}  # normalized 1.0, 0.5, 0.0
        sparse = [_r("c", 12.0), _r("d", 6.0)]  # normalized 1.0, 0.0
        fused = fuse_convex(dense, sparse, {d: {} for d in dense}, dense_weight=0.75)
        scores = {r.id: r.score for r in fused}
        assert scores == pytest.approx({"a": 0.75, "b": 0.375, "c": 0.25, "d": 0.0})
        assert [r.id for r in fused] == ["a", "b", "c", "d"]
        assert fused[-1].payload == {"doc": "d"}  # keyword-only hits keep their payload

    def test_strong_keyword_hit_can_win_when_dense_is_close(self) -> None:
        dense = {"a": 0.81, "b": 0.80, "x": 0.50}
        fused = fuse_convex(dense, [_r("b", 9.0), _r("a", 1.0)], {d: {} for d in dense})
        assert fused[0].id == "b"

    def test_single_result_legs_do_not_divide_by_zero(self) -> None:
        fused = fuse_convex({"a": 0.5}, [_r("a", 3.0)], {"a": {}})
        assert fused[0].score == pytest.approx(1.0)

    def test_weight_extremes_reproduce_each_leg(self) -> None:
        dense = {"a": 0.9, "b": 0.1}
        sparse = [_r("b", 5.0), _r("a", 1.0)]
        assert fuse_convex(dense, sparse, {}, dense_weight=1.0)[0].id == "a"
        assert fuse_convex(dense, sparse, {}, dense_weight=0.0)[0].id == "b"


class TestStrategy:
    def test_factory_default_is_convex(self) -> None:
        assert isinstance(create_fusion_strategy(), ConvexFusionStrategy)

    def test_strategy_fuses_dense_then_keyword_lists(self) -> None:
        fused = ConvexFusionStrategy(0.75).fuse([[_r("a", 0.9), _r("b", 0.2)], [_r("b", 4.0)]])
        assert [r.id for r in fused] == ["a", "b"]

    def test_unknown_strategy_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="convex"):
            create_fusion_strategy("borda")


class TestPipelineUsesConfiguredFusion:
    async def _run(self, strategy: str) -> list[str]:
        from tests.unit.test_retrieval_score_breakdown import A, B, C, _build, _dense

        orch = _build(
            _dense(A, B),  # dense scores 1.0, 0.9
            sparse=[
                SearchResult(id=C, score=9.0, payload={}),
                SearchResult(id=B, score=8.0, payload={}),
            ],
            hybrid_search_enabled=True,
            fusion_strategy=strategy,
            hybrid_dense_weight=0.75,
        )
        result = await orch.run_retrieval_pipeline(text="refund policy", corpus_id=uuid.uuid4())
        return [r.id for r in result.top_results]

    async def test_convex_and_rrf_are_both_honoured(self) -> None:
        from tests.unit.test_retrieval_score_breakdown import A, B

        # Convex: A = .75*1 = .75, C = .25*1 = .25, B = 0 (bottom of both legs).
        assert (await self._run("convex"))[0] == A
        # RRF: B = 2/(60+2) beats A = 1/(60+1) for appearing in both lists.
        assert (await self._run("rrf"))[0] == B


def test_corpus_fusion_settings_reach_the_orchestrator() -> None:
    from callobuzz_rag.api.routes.query import _build_orchestrator
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.core.config_resolver import ConfigResolver

    settings = Settings()
    resolved = ConfigResolver(settings).resolve_retrieval(
        {"fusion_strategy": "rrf", "rrf_k": 15, "hybrid_dense_weight": 0.4, "mmr_lambda": 0.9}
    )
    request = MagicMock()
    request.app.state.settings = settings
    with (
        patch("callobuzz_rag.retrieval.orchestrator.QueryOrchestrator") as orch,
        patch("callobuzz_rag.llm.litellm_provider.LiteLLMProvider.from_resolved_config"),
    ):
        _build_orchestrator(
            request,
            MagicMock(),
            SimpleNamespace(qdrant_collection="c", applied_embedding=None, embedding_config=None),
            MagicMock(),
            MagicMock(),
            resolved_retrieval=resolved,
        )
    used = orch.call_args.kwargs["settings"]
    assert (used.fusion_strategy, used.rrf_k, used.hybrid_dense_weight, used.mmr_lambda) == (
        "rrf",
        15,
        0.4,
        0.9,
    )
    assert settings.fusion_strategy == "convex"  # the shared settings are not mutated
