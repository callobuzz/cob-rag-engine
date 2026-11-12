"""Unit tests for the BM25 sparse vectors behind the keyword leg."""

import pytest

from callobuzz_rag.retrieval.sparse import (
    SparseConfig,
    SparseVectorData,
    analyze,
    query_to_sparse_vector,
    text_to_sparse_vector,
    token_to_index,
    tokenize,
)

EN = SparseConfig()
RAW = SparseConfig(language="none")


def _weights(vec: SparseVectorData) -> dict[int, float]:
    return dict(zip(vec.indices, vec.values, strict=True))


def _score(query: str, doc: str, config: SparseConfig = EN) -> float:
    """Dot product, which Qdrant computes (times IDF, constant within a test)."""
    q, d = (
        _weights(query_to_sparse_vector(query, config)),
        _weights(text_to_sparse_vector(doc, config)),
    )
    return sum(w * d.get(i, 0.0) for i, w in q.items())


class TestTokenize:
    def test_basic_english(self) -> None:
        tokens = tokenize("Hello world, this is a test!")
        assert {"hello", "world", "test"} <= set(tokens)

    def test_filters_short_tokens(self) -> None:
        assert tokenize("I am a big dog") == ["am", "big", "dog"]

    def test_empty_and_punctuation(self) -> None:
        assert tokenize("") == []
        assert tokenize("!!!...???") == []


class TestAnalyze:
    def test_stems_and_drops_stopwords(self) -> None:
        assert analyze("The shipping of the boxes was shipped", EN) == ["ship", "box", "ship"]

    def test_identifiers_survive(self) -> None:
        # "ROLLER-10": the digit token is the one that tells SKUs apart.
        assert analyze("stock for ROLLER-10", EN) == ["stock", "roller", "10"]
        assert analyze("order ab12cd", EN) == ["order", "ab12cd"]

    def test_language_none_is_plain_tokens(self) -> None:
        assert analyze("The boxes", RAW) == ["the", "boxes"]

    def test_unknown_language_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Snowball"):
            analyze("x", SparseConfig(language="klingon"))


class TestTokenToIndex:
    def test_deterministic_and_non_negative(self) -> None:
        assert token_to_index("hello") == token_to_index("hello")
        assert token_to_index("hello") != token_to_index("world")
        assert all(token_to_index(w) >= 0 for w in ["hello", "test", "machine"])


class TestDocumentVector:
    def test_bm25_saturates_repeated_terms(self) -> None:
        once = _weights(text_to_sparse_vector("refund", EN))[token_to_index("refund")]
        many = _weights(text_to_sparse_vector("refund " * 20, EN))[token_to_index("refund")]
        assert many > once
        assert many < EN.k1 + 1  # BM25's ceiling, however often a term repeats

    def test_longer_documents_weigh_a_term_less(self) -> None:
        idx = token_to_index("refund")
        short = _weights(text_to_sparse_vector("refund policy", EN))[idx]
        padded = "refund policy " + "warehouse logistics timeline " * 60
        long = _weights(text_to_sparse_vector(padded, EN))[idx]
        assert long < short

    def test_empty_sorted_deterministic(self) -> None:
        assert text_to_sparse_vector("", EN).is_empty()
        assert text_to_sparse_vector("the and of", EN).is_empty()  # stopwords only
        vec = text_to_sparse_vector("zebra apple mango banana cherry", EN)
        assert vec.indices == sorted(vec.indices)
        again = text_to_sparse_vector("zebra apple mango banana cherry", EN)
        assert (vec.indices, vec.values) == (again.indices, again.values)


class TestQueryVector:
    def test_unit_weight_per_distinct_term(self) -> None:
        vec = query_to_sparse_vector("refunds refund refunded", EN)
        assert vec.values == [1.0]  # all three stem to "refund"

    def test_plural_query_matches_singular_document(self) -> None:
        # Used to need a hand-written plural table on the query side only.
        assert _score("Do you repair other brands?", "We repair gear from any brand.") > 0
        assert _score("warranties", "Our warranty covers two years.") > 0

    def test_stopwords_do_not_score(self) -> None:
        assert _score("what is the", "what is the answer") == 0.0

    def test_sku_scores_only_the_matching_product(self) -> None:
        right = _score("ROLLER-10 stock", "ROLLER-10 foam roller, 12 in stock")
        wrong = _score("ROLLER-10 stock", "ROLLER-20 foam roller, 12 in stock")
        assert right > wrong
