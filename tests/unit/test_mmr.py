"""Unit tests for MMR diversity re-ranking."""

from callobuzz_rag.retrieval.mmr import (
    MMRItem,
    jaccard_similarity,
    mmr_rerank,
    tokenize,
)


class TestTokenize:
    def test_basic(self) -> None:
        tokens = tokenize("Hello World 123")
        assert tokens == {"hello", "world", "123"}

    def test_empty_string(self) -> None:
        assert tokenize("") == set()

    def test_special_chars_stripped(self) -> None:
        tokens = tokenize("hello-world! foo@bar")
        assert tokens == {"hello", "world", "foo", "bar"}

    def test_underscores_kept(self) -> None:
        tokens = tokenize("snake_case var_name")
        assert "snake_case" in tokens
        assert "var_name" in tokens

    def test_lowercased(self) -> None:
        tokens = tokenize("UPPER lower MiXeD")
        assert tokens == {"upper", "lower", "mixed"}


class TestJaccardSimilarity:
    def test_identical_sets(self) -> None:
        assert jaccard_similarity({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint_sets(self) -> None:
        assert jaccard_similarity({"a"}, {"b"}) == 0.0

    def test_partial_overlap(self) -> None:
        sim = jaccard_similarity({"a", "b"}, {"b", "c"})
        assert abs(sim - 1 / 3) < 1e-9

    def test_both_empty(self) -> None:
        assert jaccard_similarity(set(), set()) == 1.0

    def test_one_empty(self) -> None:
        assert jaccard_similarity(set(), {"a"}) == 0.0
        assert jaccard_similarity({"a"}, set()) == 0.0

    def test_subset(self) -> None:
        sim = jaccard_similarity({"a"}, {"a", "b"})
        assert abs(sim - 0.5) < 1e-9


class TestMMRRerank:
    def test_empty_list(self) -> None:
        assert mmr_rerank([]) == []

    def test_single_item(self) -> None:
        items = [MMRItem(id="a", score=0.9, content="hello world")]
        result = mmr_rerank(items)
        assert len(result) == 1
        assert result[0].id == "a"

    def test_lambda_1_pure_relevance(self) -> None:
        items = [
            MMRItem(id="a", score=0.5, content="foo"),
            MMRItem(id="b", score=0.9, content="foo"),
            MMRItem(id="c", score=0.7, content="foo"),
        ]
        result = mmr_rerank(items, lambda_=1.0)
        assert [r.id for r in result] == ["b", "c", "a"]

    def test_identical_content_penalized(self) -> None:
        items = [
            MMRItem(id="a", score=0.9, content="the quick brown fox"),
            MMRItem(id="b", score=0.85, content="the quick brown fox"),
            MMRItem(id="c", score=0.5, content="completely different text here"),
        ]
        result = mmr_rerank(items, lambda_=0.5)
        # "a" should be first (highest score)
        assert result[0].id == "a"
        # "c" should beat "b" due to diversity even though lower score
        assert result[1].id == "c"
        assert result[2].id == "b"

    def test_all_items_returned(self) -> None:
        items = [MMRItem(id=str(i), score=float(i) / 10, content=f"text {i}") for i in range(5)]
        result = mmr_rerank(items)
        assert len(result) == 5
        assert set(r.id for r in result) == set(str(i) for i in range(5))

    def test_lambda_clamped_above_1(self) -> None:
        items = [
            MMRItem(id="a", score=0.9, content="x"),
            MMRItem(id="b", score=0.5, content="y"),
        ]
        result = mmr_rerank(items, lambda_=1.5)
        # Should behave like lambda=1.0
        assert result[0].id == "a"

    def test_lambda_clamped_below_0(self) -> None:
        items = [
            MMRItem(id="a", score=0.9, content="x"),
            MMRItem(id="b", score=0.5, content="y"),
        ]
        result = mmr_rerank(items, lambda_=-0.5)
        assert len(result) == 2

    def test_lambda_0_pure_diversity(self) -> None:
        items = [
            MMRItem(id="a", score=0.9, content="identical text here"),
            MMRItem(id="b", score=0.85, content="identical text here"),
            MMRItem(id="c", score=0.1, content="totally different content"),
        ]
        result = mmr_rerank(items, lambda_=0.0)
        assert result[0].id == "a"  # First selected by score normalization
        # Next should be the most different
        assert result[1].id == "c"

    def test_equal_scores_normalized(self) -> None:
        items = [
            MMRItem(id="a", score=0.5, content="text a"),
            MMRItem(id="b", score=0.5, content="text b"),
        ]
        result = mmr_rerank(items)
        assert len(result) == 2

    def test_tiebreaker_uses_original_score(self) -> None:
        items = [
            MMRItem(id="a", score=0.9, content="alpha beta gamma"),
            MMRItem(id="b", score=0.8, content="delta epsilon zeta"),
        ]
        # Both completely different, so diversity penalty is 0 for both
        result = mmr_rerank(items, lambda_=0.7)
        assert result[0].id == "a"  # Higher original score wins

    def test_no_diversity_penalty_for_different_content(self) -> None:
        items = [
            MMRItem(id="a", score=0.9, content="cats dogs pets"),
            MMRItem(id="b", score=0.8, content="math science physics"),
        ]
        result = mmr_rerank(items, lambda_=0.7)
        # No diversity penalty, so order by relevance
        assert result[0].id == "a"
        assert result[1].id == "b"
