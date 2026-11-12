r"""Word-splitting call sites other than BM25 keep Indic words whole.

Each of these used `\w`, `\b` or `[a-z0-9]`, all of which cut a word at a vowel sign.
"""

import re

import pytest

from callobuzz_rag.chunking.chunker import SENTENCE_SPLIT_RE
from callobuzz_rag.conversation.answering import _answers_question, _terms
from callobuzz_rag.conversation.handoff.triggers import keyword_hits
from callobuzz_rag.conversation.language import detect_language
from callobuzz_rag.conversation.replies import _keywords
from callobuzz_rag.conversation.rules import normalize_message, rule_labeler
from callobuzz_rag.conversation.safety import abuse_level
from callobuzz_rag.enrichment.extractive import SENTENCE_RE
from callobuzz_rag.retrieval.mmr import jaccard_similarity, tokenize
from callobuzz_rag.retrieval.preprocessing import preprocess_query


def test_mmr_tokens_for_hindi_are_not_empty() -> None:
    assert tokenize("होम कलेक्शन की कीमत") == {"होम", "कलेक्शन", "की", "कीमत"}
    assert jaccard_similarity(tokenize("सोफा की कीमत"), tokenize("सोफा की छूट")) == pytest.approx(
        0.5
    )
    assert tokenize("Hello, World_1!") == {"hello", "world_1"}


def test_refusal_word_overlap_uses_whole_hindi_words() -> None:
    assert _terms("डिलीवरी में कितने दिन लगते हैं") == {"डिलीवरी", "दिन", "लगते"}
    assert _terms("What is the delivery time for sofas") == {"delivery", "time", "sofa"}
    # A continuation sharing the content words answers the question; an unrelated one does not.
    question = "डिलीवरी कितने दिन में होगी"
    assert _answers_question("डिलीवरी 5 से 7 दिन में होती है", question)
    assert not _answers_question("हमारा शोरूम कीर्ति नगर में है", question)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("সোফার দাম কত", {"সোফার", "দাম"}),
        ("சோபா விலை என்ன", {"சோபா", "விலை", "என்ன"}),
        ("ਸੋਫ਼ਾ ਕੀਮਤ ਦੱਸੋ", {"ਸੋਫ਼ਾ", "ਕੀਮਤ", "ਦੱਸੋ"}),
    ],
)
def test_source_chip_keywords_for_other_indic_scripts(text: str, expected: set[str]) -> None:
    assert _keywords(text) == expected


def test_source_chip_keywords_english_and_hinglish_unchanged() -> None:
    assert _keywords("What is the warranty on sofa kya hai") == {"warranty", "sofa"}


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("সোফার দাম কত এবং ডেলিভারি কখন হবে", "bn"),
        ("சோபாவின் விலை என்ன மற்றும் டெலிவரி எப்போது", "ta"),
        ("होम कलेक्शन की कीमत क्या है", "hi"),
    ],
)
def test_language_detection(text: str, lang: str) -> None:
    assert detect_language(text).code == lang


def test_message_normalisation_keeps_every_indic_script() -> None:
    assert normalize_message("সোফার দাম, কত?!") == "সোফার দাম কত"
    assert normalize_message("சோபா விலை என்ன?") == "சோபா விலை என்ன"
    assert normalize_message("नमस्ते।  कैसे हैं आप?") == "नमस्ते कैसे हैं आप"


def test_devanagari_greeting_followed_by_a_question_is_split_off() -> None:
    outcome = rule_labeler().classify("नमस्ते, कीमत क्या है")
    assert outcome.greeted
    assert outcome.remainder == "कीमत क्या है"


def test_profanity_matches_whole_words_only() -> None:
    assert abuse_level("तुम पागल हो") == 1
    assert abuse_level("तुम पागलपन करते हो") == 0  # a longer word that merely starts with it
    assert abuse_level("कीमत क्या है") == 0


def test_handoff_keyword_needs_a_word_boundary_in_hindi() -> None:
    assert keyword_hits("मुझे रिफंड चाहिए", ["रिफंड"]) == ["रिफंड"]
    # Latin keywords match on word boundaries; marks no longer count as a boundary.
    assert keyword_hits("sofa kharidna hai", ["sofa"]) == ["sofa"]
    assert keyword_hits("sofas", ["sofa"]) == []


def test_sentence_splitters_break_on_the_danda() -> None:
    text = "यह पहला वाक्य है। यह दूसरा है॥ और तीसरा? हाँ!"
    assert SENTENCE_SPLIT_RE.split(text) == [
        "यह पहला वाक्य है।",
        "यह दूसरा है॥",
        "और तीसरा?",
        "हाँ!",
    ]
    assert SENTENCE_RE.split(text) == SENTENCE_SPLIT_RE.split(text)
    assert SENTENCE_SPLIT_RE.split("One. Two! Three?") == ["One.", "Two!", "Three?"]


def test_query_preprocessing_keeps_hindi_words_whole() -> None:
    result = preprocess_query("होम कलेक्शन की कीमत क्या है")
    assert result.keywords == ["होम", "कलेक्शन", "कीमत"]


def test_gap_clusters_get_whole_hindi_keywords() -> None:
    from sklearn.feature_extraction.text import CountVectorizer

    from callobuzz_rag.gaps.detector import _topic_vectorizer

    vec = _topic_vectorizer()
    assert isinstance(vec, CountVectorizer)
    names = set(vec.fit(["डिलीवरी में कितने दिन लगते हैं", "डिलीवरी शुल्क क्या है"]).get_feature_names_out())
    assert "डिलीवरी" in names
    assert "है" not in names
    assert not any(re.fullmatch(r"[ा-्]+", n) for n in names)
