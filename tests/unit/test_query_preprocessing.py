"""Unit tests for query preprocessing."""

from callobuzz_rag.retrieval.preprocessing import (
    STOP_WORDS_EN,
    STOP_WORDS_ES,
    STOP_WORDS_KO,
    STOP_WORDS_ZH,
    _normalize,
    _strip_korean_particles,
    _tokenize_chinese,
    _tokenize_korean,
    _validate_keywords,
    preprocess_query,
)


class TestNormalize:
    def test_whitespace_collapse(self) -> None:
        result = _normalize("  hello   world  ")
        assert result == "hello world"

    def test_lowercase(self) -> None:
        assert _normalize("Hello World") == "hello world"

    def test_strip_punctuation(self) -> None:
        assert _normalize("...hello!") == "hello"

    def test_empty(self) -> None:
        assert _normalize("") == ""


class TestStopWords:
    def test_en_has_common_words(self) -> None:
        assert "the" in STOP_WORDS_EN
        assert "is" in STOP_WORDS_EN
        assert "and" in STOP_WORDS_EN

    def test_es_has_common_words(self) -> None:
        assert "el" in STOP_WORDS_ES
        assert "de" in STOP_WORDS_ES

    def test_ko_has_particles(self) -> None:
        assert "\uc774" in STOP_WORDS_KO  # 이
        assert "\uadf8" in STOP_WORDS_KO  # 그

    def test_zh_has_common_words(self) -> None:
        assert "\u7684" in STOP_WORDS_ZH  # 的
        assert "\u662f" in STOP_WORDS_ZH  # 是


class TestPreprocessQuery:
    def test_english_basic(self) -> None:
        result = preprocess_query("What is machine learning?")
        assert result.language == "en"
        assert "machine" in result.keywords
        assert "learning" in result.keywords
        # "what" and "is" should be removed as stop words
        assert "what" not in result.keywords
        assert "is" not in result.keywords

    def test_empty_query(self) -> None:
        result = preprocess_query("")
        assert result.is_empty
        assert result.language == "en"

    def test_preserves_original(self) -> None:
        original = "What is machine learning?"
        result = preprocess_query(original)
        assert result.original == original

    def test_short_words_filtered(self) -> None:
        result = preprocess_query("I am a AI ML expert")
        # "I", "am", "a" filtered (stop words or < 3 chars)
        # "AI", "ML" filtered (< 3 chars)
        assert "expert" in result.keywords

    def test_pure_numbers_filtered(self) -> None:
        result = preprocess_query("top 10 best practices 2024")
        assert "2024" not in result.keywords
        assert "10" not in result.keywords
        assert "best" in result.keywords
        assert "practices" in result.keywords

    def test_keyword_text_property(self) -> None:
        result = preprocess_query("machine learning algorithms")
        assert isinstance(result.keyword_text, str)
        assert len(result.keyword_text) > 0


class TestChineseTokenization:
    def test_bigrams(self) -> None:
        tokens = _tokenize_chinese("\u673a\u5668\u5b66\u4e60")  # 机器学习
        assert "\u673a\u5668" in tokens  # 机器
        assert "\u5668\u5b66" in tokens  # 器学
        assert "\u5b66\u4e60" in tokens  # 学习

    def test_includes_individual_chars(self) -> None:
        tokens = _tokenize_chinese("\u673a\u5668")  # 机器
        assert "\u673a" in tokens  # 机
        assert "\u5668" in tokens  # 器

    def test_mixed_cjk_latin(self) -> None:
        tokens = _tokenize_chinese("\u673a\u5668\u5b66\u4e60\u662fai\u6280\u672f")
        assert "ai" in tokens
        assert "\u673a\u5668" in tokens  # 机器


class TestKoreanTokenization:
    def test_particle_stripping(self) -> None:
        stripped = _strip_korean_particles("\ud559\uad50\uc5d0\uc11c")  # 학교에서
        assert stripped == "\ud559\uad50"  # 학교

    def test_no_particle(self) -> None:
        stripped = _strip_korean_particles("\ucef4\ud4e8\ud130")  # 컴퓨터
        assert stripped == "\ucef4\ud4e8\ud130"

    def test_short_stem_filtered(self) -> None:
        # Single-syllable stems should be filtered
        tokens = _tokenize_korean("\ub098\ub294")  # 나는
        # "나" is 1 syllable after stripping "는", too short
        assert "\ub098" not in tokens  # 나


class TestValidateKeywords:
    def test_latin_min_length(self) -> None:
        result = _validate_keywords(["hi", "hello", "ok", "test"], "en")
        assert "hi" not in result
        assert "ok" not in result
        assert "hello" in result
        assert "test" in result

    def test_numbers_filtered(self) -> None:
        result = _validate_keywords(["hello", "123", "world"], "en")
        assert "123" not in result

    def test_cjk_keeps_short(self) -> None:
        result = _validate_keywords(["\u673a", "\u5668", "\u5b66\u4e60"], "zh")
        assert "\u673a" in result  # Single CJK chars are valid
