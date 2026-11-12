"""`category_weights` — the caller's per-category trust, as data.

A client application re-implemented this downstream as a `sourceAuthority` factor
blended into a score it recomputed from the text it got back. It belongs here, where the category
is already in the payload and the weight can still change WHICH chunks come back
rather than only how the caller sorts the ones it was given.
"""

import uuid

import pytest
from pydantic import ValidationError

from callobuzz_rag.api.schemas.query import QueryOptions
from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator
from callobuzz_rag.retrieval.reranking.base import RerankResult

from .test_retrieval_score_breakdown import A, B, C, _build, _dense

CATEGORIES = {A: "operations", B: "memory", C: "product"}


def _orch(**overrides: object) -> QueryOrchestrator:
    return _build(_dense(A, B, C, categories=CATEGORIES), **overrides)


@pytest.mark.asyncio
async def test_no_weights_leaves_ranking_untouched() -> None:
    """The option is inert when absent — not 'defaults to something'."""
    result = await _orch().run_retrieval_pipeline(text="q", corpus_id=uuid.uuid4())

    assert [r.id for r in result.top_results] == [A, B, C]
    assert result.score_breakdown[A].category_weight is None
    # The category is still reported: knowing what a chunk IS does not require
    # having an opinion about it.
    assert result.score_breakdown[A].category == "operations"


@pytest.mark.asyncio
async def test_a_weight_can_reorder_the_answer() -> None:
    result = await _orch().run_retrieval_pipeline(
        text="q",
        corpus_id=uuid.uuid4(),
        category_weights={"product": 2.0},
    )

    # C was fused last and is now first, purely on the caller's policy.
    assert [r.id for r in result.top_results] == [C, A, B]
    assert result.score_breakdown[C].category_weight == 2.0
    assert result.score_breakdown[C].pipeline_rank == 1


@pytest.mark.asyncio
async def test_the_unweighted_fusion_score_survives_in_the_breakdown() -> None:
    """`score` becomes a policy-adjusted number; `fused_score` stays the evidence."""
    result = await _orch().run_retrieval_pipeline(
        text="q",
        corpus_id=uuid.uuid4(),
        category_weights={"memory": 0.5},
    )

    breakdown = result.score_breakdown[B]
    weighted = next(r for r in result.top_results if r.id == B)
    assert breakdown.fused_score == pytest.approx(1 / 62)  # rank 2, RRF k=60
    assert weighted.score == pytest.approx(breakdown.fused_score * 0.5)


@pytest.mark.asyncio
async def test_an_unnamed_category_is_neutral_not_demoted() -> None:
    """An omission is 'no opinion'. One client's local map defaulted unknowns to 0.6,
    which quietly demoted every category it had never heard of."""
    result = await _orch().run_retrieval_pipeline(
        text="q",
        corpus_id=uuid.uuid4(),
        category_weights={"operations": 1.0},
    )

    assert [r.id for r in result.top_results] == [A, B, C]
    assert result.score_breakdown[B].category_weight == 1.0
    assert result.score_breakdown[C].category_weight == 1.0


@pytest.mark.asyncio
async def test_a_policy_that_matches_nothing_changes_nothing() -> None:
    """Found live, not in review: `{"product": 3.0}` against a pool holding no
    product chunks returned a DIFFERENT five, because the weighting step re-sorted
    the pool by fusion score and threw away the order the reranker chose. A policy
    with nothing to say must be byte-identical to sending no policy at all.
    """
    dense = _dense(A, B, C, categories={A: "operations", B: "operations", C: "operations"})
    # The cross-encoder inverts fusion order, so a re-sort by fusion score is
    # visible: without the guard this comes back A, B, C.
    reranked = [
        RerankResult(index=2, score=0.9, text="c"),
        RerankResult(index=1, score=0.5, text="b"),
        RerankResult(index=0, score=0.1, text="a"),
    ]
    neutral = await _build(dense, reranked=reranked).run_retrieval_pipeline(
        text="q", corpus_id=uuid.uuid4(), category_weights={"product": 3.0}
    )
    unweighted = await _build(dense, reranked=reranked).run_retrieval_pipeline(
        text="q", corpus_id=uuid.uuid4()
    )

    assert [r.id for r in neutral.top_results] == [C, B, A]
    assert [r.id for r in neutral.top_results] == [r.id for r in unweighted.top_results]
    # Still reported, so the caller can see the engine had no opinion.
    assert neutral.score_breakdown[A].category_weight == 1.0


@pytest.mark.asyncio
async def test_a_chunk_with_no_category_is_neutral() -> None:
    orch = _build(_dense(A, B, C, categories={A: "operations"}))

    result = await orch.run_retrieval_pipeline(
        text="q",
        corpus_id=uuid.uuid4(),
        category_weights={"operations": 0.1},
    )

    # A is demoted hard; the two uncategorised chunks keep their relative order.
    assert [r.id for r in result.top_results] == [B, C, A]
    assert result.score_breakdown[B].category is None
    assert result.score_breakdown[B].category_weight == 1.0


@pytest.mark.asyncio
async def test_weights_apply_before_the_final_k_trim() -> None:
    """The whole point: a weight must be able to pull a chunk INTO the answer.
    Weighting only what already survived the trim would be cosmetic."""
    orch = _build(_dense(A, B, C, categories=CATEGORIES), query_final_k=1)

    result = await orch.run_retrieval_pipeline(
        text="q",
        corpus_id=uuid.uuid4(),
        category_weights={"product": 3.0},
    )

    assert [r.id for r in result.top_results] == [C]


class TestValidation:
    """A bad weight must 422, not be clamped — silence is how defect 1 survived."""

    @pytest.mark.parametrize("weight", [0, -1.0, 10.1])
    def test_out_of_range_weight_is_rejected(self, weight: float) -> None:
        with pytest.raises(ValidationError, match="must be in"):
            QueryOptions(category_weights={"operations": weight})

    def test_zero_is_rejected_and_points_at_the_filter(self) -> None:
        """Zero means 'never return this', which is a filter, not a weight."""
        with pytest.raises(ValidationError, match="`category` filter"):
            QueryOptions(category_weights={"memory": 0.0})

    def test_too_many_weights_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="At most 32"):
            QueryOptions(category_weights={f"c{i}": 1.0 for i in range(33)})

    def test_empty_category_name_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="1-50 characters"):
            QueryOptions(category_weights={"": 1.0})

    def test_a_valid_map_round_trips(self) -> None:
        options = QueryOptions(category_weights={"operations": 1.1, "memory": 0.9})
        assert options.category_weights == {"operations": 1.1, "memory": 0.9}
