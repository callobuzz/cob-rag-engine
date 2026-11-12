"""BM25 keyword vectors on Indic text: whole words, Hindi stop words, no Latin stemming."""

import math
import re

import pytest

import callobuzz_rag.retrieval.sparse as sparse
from callobuzz_rag.retrieval.sparse import (
    SparseConfig,
    analyze,
    query_to_sparse_vector,
    text_to_sparse_vector,
    token_to_index,
    tokenize,
)
from tests.unit.indic_corpus import DOCS, QUERIES

EN = SparseConfig()


class TestAnalyzeIndic:
    def test_hindi_words_are_whole_and_particles_dropped(self) -> None:
        assert analyze("होम कलेक्शन की कीमत क्या है", EN) == ["होम", "कलेक्शन", "कीमत"]

    def test_particles_from_the_brief_are_dropped(self) -> None:
        for word in "का की के है हैं में से को और यह वह एक पर भी तो ही था थे थी नहीं क्या".split():  # noqa: SIM905
            assert analyze(f"फर्नीचर {word}", EN) == ["फर्नीचर"], word

    def test_content_words_that_look_like_particles_survive(self) -> None:
        # "कर" (do / tax), "बात" (matter) and "कीमत" (price) carry meaning.
        assert analyze("टैक्स कर बात कीमत", EN) == ["टैक्स", "कर", "बात", "कीमत"]

    def test_romanized_hindi_function_words_are_dropped(self) -> None:
        got = analyze("order ki delivery kab hogi aur kya nahi hai toh refund mein", EN)
        assert got == ["order", "deliveri", "refund"]

    def test_hinglish_stopwords_do_not_remove_real_english_content(self) -> None:
        assert analyze("pay par person near me", EN) == ["pay", "par", "person", "near"]

    def test_non_latin_tokens_are_not_stemmed(self) -> None:
        # The English Snowball stemmer must leave Indic tokens as they are.
        for text in ["गद्दों गद्दे गद्दा", "ਆਰਡਰਾਂ ਆਰਡਰ", "ഓർഡറുകൾ ഓർഡർ"]:
            assert analyze(text, EN) == tokenize(text)

    def test_latin_tokens_are_still_stemmed_next_to_indic(self) -> None:
        assert analyze("shipped boxes गद्दे", EN) == ["ship", "box", "गद्दे"]

    def test_accented_latin_is_stemmed_whole(self) -> None:
        assert analyze("cafés résumés", EN)[0].startswith("caf")

    def test_language_none_keeps_every_token(self) -> None:
        assert analyze("होम की कीमत", SparseConfig(language="none")) == ["होम", "की", "कीमत"]

    def test_non_latin_snowball_languages_still_stem_their_script(self) -> None:
        assert analyze("الكتاب", SparseConfig(language="arabic")) != []


class TestSamePathForDocumentsAndQueries:
    def test_query_and_document_share_term_indices(self) -> None:
        doc = text_to_sparse_vector("होम कलेक्शन की कीमत 12999 रुपये", EN)
        query = query_to_sparse_vector("कीमत क्या है", EN)
        assert set(query.indices) <= set(doc.indices)
        assert query.indices == [token_to_index("कीमत")]

    def test_nfc_variants_hit_the_same_index(self) -> None:
        a = query_to_sparse_vector("क़", EN)
        b = text_to_sparse_vector("ज़ रुपये क़", EN)
        assert set(a.indices) <= set(b.indices)


def _keyword_leg(config: SparseConfig = EN) -> tuple[float, float]:
    """hit@1 and MRR of BM25 (document vector x query vector x IDF) on the Hindi corpus."""
    ids = list(DOCS)
    docs = {}
    for doc_id in ids:
        vec = text_to_sparse_vector(DOCS[doc_id], config)
        docs[doc_id] = dict(zip(vec.indices, vec.values, strict=True))
    df: dict[int, int] = {}
    for weights in docs.values():
        for idx in weights:
            df[idx] = df.get(idx, 0) + 1
    n = len(ids)

    def idf(idx: int) -> float:
        return math.log(1 + (n - df[idx] + 0.5) / (df[idx] + 0.5))

    hits = 0
    reciprocal = 0.0
    for question, answer in QUERIES:
        query = query_to_sparse_vector(question, config)
        score = {
            i: sum(idf(ix) * docs[i][ix] for ix in query.indices if ix in docs[i]) for i in ids
        }
        order = sorted(ids, key=lambda i: (-score[i], i))
        rank = order.index(answer) + 1 if score[answer] > 0 else None
        hits += rank == 1
        reciprocal += 1 / rank if rank else 0.0
    return hits / len(QUERIES), reciprocal / len(QUERIES)


class TestHindiKeywordLeg:
    """20 Hindi passages, 10 questions, the keyword leg alone (no dense vectors)."""

    def test_bar(self) -> None:
        hit, mrr = _keyword_leg()
        # Measured 0.80 / 0.883 (was 0.70 / 0.750 with the fragmenting tokenizer).
        assert hit >= 0.8
        assert mrr >= 0.85

    def test_old_tokenizer_scores_lower(self, monkeypatch: pytest.MonkeyPatch) -> None:
        new_hit, new_mrr = _keyword_leg()
        old = re.compile(r"[\w]+")

        def old_tokenize(text: str, min_length: int) -> list[str]:
            return [t for t in old.findall(text.lower()) if len(t) >= min_length]

        monkeypatch.setattr(sparse, "_tokenize", old_tokenize)
        old_hit, old_mrr = _keyword_leg()
        assert (old_hit, old_mrr) < (new_hit, new_mrr)
        assert old_hit <= 0.7
