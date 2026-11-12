"""The shared word tokenizer: combining marks belong to the word (Indic scripts)."""

import re
import time

import pytest

from callobuzz_rag.text.tokenize import (
    tokenize,
    word_pattern,
    word_set,
)

# (script, sentence, the words it must split into)
SENTENCES = [
    ("hindi", "होम कलेक्शन की कीमत क्या है", ["होम", "कलेक्शन", "की", "कीमत", "क्या", "है"]),
    ("marathi", "तुमची ऑर्डर कधी पोहोचेल", ["तुमची", "ऑर्डर", "कधी", "पोहोचेल"]),
    ("bengali", "আমার অর্ডার কখন পৌঁছাবে", ["আমার", "অর্ডার", "কখন", "পৌঁছাবে"]),
    ("tamil", "என் ஆர்டர் எப்போது வரும்", ["என்", "ஆர்டர்", "எப்போது", "வரும்"]),
    ("telugu", "నా ఆర్డర్ ఎప్పుడు వస్తుంది", ["నా", "ఆర్డర్", "ఎప్పుడు", "వస్తుంది"]),
    ("gujarati", "મારો ઓર્ડર ક્યારે આવશે", ["મારો", "ઓર્ડર", "ક્યારે", "આવશે"]),
    ("kannada", "ನನ್ನ ಆರ್ಡರ್ ಯಾವಾಗ ಬರುತ್ತದೆ", ["ನನ್ನ", "ಆರ್ಡರ್", "ಯಾವಾಗ", "ಬರುತ್ತದೆ"]),
    ("malayalam", "എന്റെ ഓർഡർ എപ്പോൾ വരും", ["എന്റെ", "ഓർഡർ", "എപ്പോൾ", "വരും"]),
    ("gurmukhi", "ਮੇਰਾ ਆਰਡਰ ਕਦੋਂ ਆਵੇਗਾ", ["ਮੇਰਾ", "ਆਰਡਰ", "ਕਦੋਂ", "ਆਵੇਗਾ"]),
    ("odia", "ମୋ ଅର୍ଡର କେବେ ଆସିବ", ["ମୋ", "ଅର୍ଡର", "କେବେ", "ଆସିବ"]),
]


@pytest.mark.parametrize(("script", "sentence", "words"), SENTENCES, ids=[s[0] for s in SENTENCES])
def test_indic_sentences_split_into_whole_words(
    script: str, sentence: str, words: list[str]
) -> None:
    assert tokenize(sentence) == words


def test_plain_word_class_would_shatter_hindi() -> None:
    # The bug this module exists for: `\w+` makes 11 fragments of 6 Hindi words.
    sentence = "होम कलेक्शन की कीमत क्या है"
    assert len(re.findall(r"\w+", sentence)) == 11
    assert len(tokenize(sentence)) == 6


def test_nukta_and_conjuncts_stay_inside_words() -> None:
    assert tokenize("ज़रूरत फ़ोन") == ["ज़रूरत", "फ़ोन"]
    assert tokenize("विद्यालय प्रश्न क्षत्रिय ज्ञान श्री") == [
        "विद्यालय",
        "प्रश्न",
        "क्षत्रिय",
        "ज्ञान",
        "श्री",
    ]


def test_precomposed_and_decomposed_nukta_are_one_term() -> None:
    assert tokenize("क़") == tokenize("क़")


def test_danda_and_punctuation_separate_words() -> None:
    assert tokenize("यह अच्छा है। वह नहीं॥") == ["यह", "अच्छा", "है", "वह", "नहीं"]
    assert tokenize("कीमत, छूट; डिलीवरी!") == ["कीमत", "छूट", "डिलीवरी"]


def test_zwj_and_zwnj_do_not_split_and_are_dropped() -> None:
    with_zwj = "क्‍ष"
    with_zwnj = "क्‌ष"
    assert tokenize(f"सब {with_zwj} सब") == ["सब", "क्ष", "सब"]
    assert tokenize(with_zwnj) == tokenize("क्ष") == ["क्ष"]
    # Persian: the half-space word is one word, and typed without it, the same term.
    assert tokenize("می‌خواهم") == tokenize("میخواهم")


def test_stray_joiner_or_mark_never_opens_a_word() -> None:
    assert tokenize("‍ ा ‌") == []
    assert tokenize("ab ́ cd") == ["ab", "cd"]


def test_hinglish_english_and_digits_mixed() -> None:
    text = "Mera order #4521 kab aayega? कीमत ₹12,999 hai, 2 din mein"
    assert tokenize(text) == [
        "mera", "order", "4521", "kab", "aayega", "कीमत", "12", "999", "hai", "2", "din", "mein",
    ]  # fmt: skip


def test_english_tokens_are_what_the_old_pattern_produced() -> None:
    old = re.compile(r"[\w]+", re.UNICODE)
    for text in [
        "Hello world, this is a test!",
        "stock for ROLLER-10 and ab12cd_x",
        "The shipping of the boxes was shipped",
        "Don't panic: it's 5 o'clock; e-mail me@x.com",
        "",
        "!!!...???",
    ]:
        assert tokenize(text) == old.findall(text.lower())


def test_accented_latin_stays_whole() -> None:
    assert tokenize("Café résumé naïve señor Zoë") == ["café", "résumé", "naïve", "señor", "zoë"]
    # Decomposed input (e + combining acute) composes to the same word.
    assert tokenize("café") == ["café"]


def test_min_length_counts_code_points() -> None:
    assert tokenize("a bb ccc", 2) == ["bb", "ccc"]
    assert tokenize("की व", 2) == ["की"]


def test_word_set() -> None:
    assert word_set("कीमत कीमत और") == {"कीमत", "और"}


def test_word_pattern_boundaries_see_combining_marks() -> None:
    # `\b` / `(?<!\w)` treat a vowel sign as a non-word character and match mid-word.
    pat = word_pattern("कर")
    assert pat.search("कर दो")
    assert not pat.search("करी")  # "कर" + vowel sign is another word
    assert not pat.search("आकर")
    assert word_pattern("sir", re.IGNORECASE).search("Hello Sir.")
    assert not word_pattern("sir").search("desire")


def test_tokenizing_10k_chunks_is_fast() -> None:
    english = "The quick brown fox shipped 10 boxes to the warehouse in Café Road. " * 8
    hindi = "होम कलेक्शन में सोफा और बेड की कीमत 12,999 रुपये से शुरू होती है। " * 8
    started = time.perf_counter()
    for i in range(10_000):
        tokenize(english if i % 2 else hindi)
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, f"10k chunks took {elapsed:.2f}s"
