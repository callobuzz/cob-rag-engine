"""Query preprocessing: language detection, stop-word removal, CJK tokenization.

Ported from OpenClaw src/memory/query-expansion.ts.
Supports 7 languages: EN, ES, PT, AR, KO, JA, ZH.

Usage:
    from callobuzz_rag.retrieval.preprocessing import preprocess_query
    result = preprocess_query("What is machine learning?")
    # result.language == "en"
    # result.keywords == ["machine", "learning"]
"""

import logging
import re
import unicodedata

from callobuzz_rag.retrieval.models import PreprocessedQuery
from callobuzz_rag.text.stopwords import HINDI_STOPWORDS
from callobuzz_rag.text.tokenize import tokens

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum query length for reliable language detection.
# Shorter queries default to English.
MIN_DETECT_LENGTH = 10

# Minimum keyword length for Latin-script languages (EN, ES, PT).
MIN_LATIN_KEYWORD_LENGTH = 3

# Minimum Korean stem length in syllables after particle stripping.
MIN_KOREAN_STEM_SYLLABLES = 2

# Pure number pattern — tokens that are all digits.
_PURE_NUMBER = re.compile(r"^\d+$")

# All-punctuation pattern.
_ALL_PUNCT = re.compile(r"^[^\w]+$", re.UNICODE)

# Tokenization: Unicode letters, numbers, underscores.
# (the word tokenizer is `callobuzz_rag.text.tokenize`: marks count as word characters)

# CJK Unicode ranges for script detection.
_CJK_UNIFIED = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff"
    r"\U00020000-\U0002a6df\U0002a700-\U0002b73f]"
)
_HIRAGANA = re.compile(r"[\u3040-\u309f]")
_KATAKANA = re.compile(r"[\u30a0-\u30ff]")
_HANGUL = re.compile(r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]")

# ---------------------------------------------------------------------------
# Stop-word lists (7 languages) — ported from OpenClaw query-expansion.ts
# ---------------------------------------------------------------------------

STOP_WORDS_EN: frozenset[str] = frozenset(
    {
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "aren't",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "can't",
        "cannot",
        "could",
        "couldn't",
        "did",
        "didn't",
        "do",
        "does",
        "doesn't",
        "doing",
        "don't",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "get",
        "got",
        "had",
        "hadn't",
        "has",
        "hasn't",
        "have",
        "haven't",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "isn't",
        "it",
        "it's",
        "its",
        "itself",
        "just",
        "let's",
        "me",
        "might",
        "more",
        "most",
        "mustn't",
        "my",
        "myself",
        "no",
        "nor",
        "not",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "ought",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "shan't",
        "she",
        "should",
        "shouldn't",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "wasn't",
        "we",
        "were",
        "weren't",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "won't",
        "would",
        "wouldn't",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
        # Vague references and filler words
        "thing",
        "things",
        "stuff",
        "something",
        "anything",
        "everything",
        "someone",
        "anyone",
        "everyone",
        "recently",
        "yesterday",
        "today",
        "tomorrow",
        "tell",
        "show",
        "give",
        "find",
        "know",
        "want",
        "need",
        "like",
        "please",
        "thanks",
        "thank",
    }
)

STOP_WORDS_ES: frozenset[str] = frozenset(
    {
        "a",
        "al",
        "algo",
        "algunas",
        "alguno",
        "algunos",
        "ante",
        "antes",
        "aquel",
        "aquella",
        "aquellas",
        "aquellos",
        "aqui",
        "asi",
        "aunque",
        "bien",
        "bueno",
        "cada",
        "casi",
        "como",
        "con",
        "contra",
        "cosas",
        "creo",
        "cual",
        "cuando",
        "da",
        "de",
        "del",
        "desde",
        "donde",
        "dos",
        "el",
        "ella",
        "ellas",
        "ellos",
        "en",
        "entre",
        "era",
        "esa",
        "esas",
        "ese",
        "eso",
        "esos",
        "esta",
        "estaba",
        "estado",
        "estas",
        "este",
        "esto",
        "estos",
        "fue",
        "ha",
        "hace",
        "hacia",
        "hay",
        "la",
        "las",
        "le",
        "les",
        "lo",
        "los",
        "mas",
        "me",
        "mi",
        "mia",
        "mucho",
        "muy",
        "nada",
        "ni",
        "no",
        "nos",
        "nosotros",
        "nuestro",
        "o",
        "otra",
        "otro",
        "para",
        "pero",
        "por",
        "que",
        "quien",
        "se",
        "ser",
        "si",
        "sin",
        "so",
        "sobre",
        "somos",
        "son",
        "soy",
        "su",
        "sus",
        "tambien",
        "tan",
        "te",
        "tengo",
        "ti",
        "tiene",
        "todo",
        "todos",
        "tu",
        "tus",
        "un",
        "una",
        "uno",
        "unos",
        "usted",
        "ustedes",
        "y",
        "ya",
        "yo",
    }
)

STOP_WORDS_PT: frozenset[str] = frozenset(
    {
        "a",
        "ao",
        "aos",
        "aquela",
        "aquelas",
        "aquele",
        "aqueles",
        "aquilo",
        "as",
        "ate",
        "com",
        "como",
        "da",
        "das",
        "de",
        "dela",
        "delas",
        "dele",
        "deles",
        "depois",
        "do",
        "dos",
        "e",
        "ela",
        "elas",
        "ele",
        "eles",
        "em",
        "entre",
        "era",
        "essa",
        "essas",
        "esse",
        "esses",
        "esta",
        "estas",
        "este",
        "estes",
        "eu",
        "foi",
        "for",
        "ha",
        "isso",
        "isto",
        "ja",
        "la",
        "lhe",
        "lhes",
        "lo",
        "mais",
        "mas",
        "me",
        "meu",
        "minha",
        "muito",
        "na",
        "nas",
        "nao",
        "nem",
        "no",
        "nos",
        "nossa",
        "nosso",
        "num",
        "numa",
        "o",
        "os",
        "ou",
        "para",
        "pela",
        "pelas",
        "pelo",
        "pelos",
        "por",
        "qual",
        "quando",
        "que",
        "quem",
        "se",
        "sem",
        "ser",
        "seu",
        "sua",
        "tambem",
        "te",
        "tem",
        "toda",
        "todas",
        "todo",
        "todos",
        "tu",
        "tua",
        "tudo",
        "um",
        "uma",
        "uns",
        "voce",
        "voces",
    }
)

STOP_WORDS_AR: frozenset[str] = frozenset(
    {
        "من",
        "في",
        "على",
        "إلى",
        "عن",
        "مع",
        "هذا",
        "هذه",
        "ذلك",
        "تلك",
        "التي",
        "الذي",
        "اللذين",
        "اللتين",
        "الذين",
        "اللواتي",
        "هو",
        "هي",
        "هم",
        "هن",
        "نحن",
        "أنا",
        "أنت",
        "أنتم",
        "أنتن",
        "كان",
        "كانت",
        "كانوا",
        "يكون",
        "تكون",
        "أن",
        "إن",
        "لا",
        "ما",
        "لم",
        "لن",
        "قد",
        "و",
        "أو",
        "ثم",
        "لكن",
        "بل",
        "حتى",
        "إذا",
        "لو",
        "كل",
        "بعض",
        "غير",
        "بين",
        "فوق",
        "تحت",
        "أمام",
        "خلف",
        "عند",
        "هنا",
        "هناك",
        "الآن",
        "أيضا",
        "جدا",
        "فقط",
        "أي",
        "كيف",
        "لماذا",
        "متى",
        "أين",
    }
)

STOP_WORDS_KO: frozenset[str] = frozenset(
    {
        "이",
        "그",
        "저",
        "것",
        "수",
        "등",
        "들",
        "및",
        "에",
        "를",
        "을",
        "의",
        "가",
        "은",
        "는",
        "로",
        "으로",
        "와",
        "과",
        "도",
        "만",
        "에서",
        "에게",
        "한테",
        "께",
        "처럼",
        "같이",
        "보다",
        "까지",
        "부터",
        "마다",
        "밖에",
        "대로",
        "이다",
        "아니다",
        "있다",
        "없다",
        "하다",
        "되다",
        "않다",
        "못하다",
        "이런",
        "그런",
        "저런",
        "어떤",
        "무슨",
        "아주",
        "매우",
        "너무",
        "정말",
        "진짜",
        "참",
        "좀",
        "다",
        "더",
        "덜",
        "가장",
        "제일",
        "또",
        "또한",
        "그리고",
        "하지만",
        "그러나",
        "그래서",
        "때문에",
        "위해",
        "대해",
        "통해",
        "따라",
        "관해",
        "나",
        "너",
        "우리",
        "저희",
        "그녀",
        "그들",
        "여기",
        "거기",
        "저기",
        "지금",
        "오늘",
        "어제",
        "내일",
        "언제",
        "어디",
        "왜",
        "어떻게",
    }
)

STOP_WORDS_JA: frozenset[str] = frozenset(
    {
        "の",
        "に",
        "は",
        "を",
        "た",
        "が",
        "で",
        "て",
        "と",
        "し",
        "れ",
        "さ",
        "ある",
        "いる",
        "も",
        "する",
        "から",
        "な",
        "こと",
        "として",
        "い",
        "や",
        "れる",
        "など",
        "なっ",
        "ない",
        "この",
        "ため",
        "その",
        "あと",
        "よう",
        "また",
        "もの",
        "という",
        "あり",
        "まで",
        "られ",
        "なる",
        "へ",
        "か",
        "だ",
        "これ",
        "によって",
        "により",
        "おり",
        "より",
        "による",
        "ず",
        "なり",
        "られる",
        "において",
        "ば",
        "なかっ",
        "なく",
        "しかし",
        "について",
        "せ",
        "だっ",
        "その他",
        "できる",
        "それ",
        "ここ",
        "今",
        "何",
        "誰",
    }
)

STOP_WORDS_ZH: frozenset[str] = frozenset(
    {
        "的",
        "了",
        "在",
        "是",
        "我",
        "有",
        "和",
        "就",
        "不",
        "人",
        "都",
        "一",
        "一个",
        "上",
        "也",
        "很",
        "到",
        "说",
        "要",
        "去",
        "你",
        "会",
        "着",
        "没有",
        "看",
        "好",
        "自己",
        "这",
        "他",
        "她",
        "它",
        "们",
        "那",
        "些",
        "什么",
        "怎么",
        "怎样",
        "如何",
        "为什么",
        "哪",
        "哪里",
        "哪个",
        "谁",
        "多少",
        "几",
        "吗",
        "呢",
        "吧",
        "啊",
        "呀",
        "哦",
        "嗯",
        "把",
        "被",
        "让",
        "给",
        "对",
        "向",
        "从",
        "以",
        "为",
        "因为",
        "所以",
        "但是",
        "可是",
        "然而",
        "而且",
        "并且",
        "或者",
        "如果",
        "虽然",
        "虽",
        "即使",
        "只要",
        "无论",
        "不论",
        "不管",
        "既",
        "又",
        "还",
        "再",
        "已经",
        "正在",
        "将",
        "能",
        "可以",
        "应该",
        "必须",
        "需要",
        "想",
        "可能",
        "这个",
        "那个",
        "这些",
        "那些",
        "这里",
        "那里",
        "现在",
        "今天",
        "昨天",
        "明天",
        "最近",
        "东西",
        "事情",
    }
)

# Map language codes to stop-word sets.
_STOP_WORDS_BY_LANG: dict[str, frozenset[str]] = {
    "en": STOP_WORDS_EN,
    "es": STOP_WORDS_ES,
    "pt": STOP_WORDS_PT,
    "ar": STOP_WORDS_AR,
    "ko": STOP_WORDS_KO,
    "ja": STOP_WORDS_JA,
    "zh": STOP_WORDS_ZH,
    "zh-cn": STOP_WORDS_ZH,
    "zh-tw": STOP_WORDS_ZH,
    "hi": HINDI_STOPWORDS,
}

# ---------------------------------------------------------------------------
# Korean trailing particles — sorted by length descending for longest-match.
# Ported from OpenClaw query-expansion.ts.
# ---------------------------------------------------------------------------

_KOREAN_PARTICLES: list[str] = [
    "에서",
    "으로",
    "에게",
    "한테",
    "처럼",
    "같이",
    "보다",
    "까지",
    "부터",
    "마다",
    "밖에",
    "대로",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "에",
    "로",
    "와",
    "과",
    "도",
    "만",
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def preprocess_query(text: str) -> PreprocessedQuery:
    """Preprocess a user query for retrieval.

    Steps:
    1. Normalize whitespace and casing
    2. Detect language
    3. Tokenize (CJK-aware if needed)
    4. Remove stop words
    5. Validate keywords (length, no pure numbers/punctuation)

    Args:
        text: Raw user query string.

    Returns:
        PreprocessedQuery with language, normalized text, and keywords.
    """
    normalized = _normalize(text)

    if not normalized:
        return PreprocessedQuery(original=text, normalized="", language="en", keywords=[])

    language = _detect_language(normalized)
    is_cjk = language in {"zh", "zh-cn", "zh-tw", "ja", "ko"}

    if language in {"zh", "zh-cn", "zh-tw"}:
        tokens = _tokenize_chinese(normalized)
    elif language == "ja":
        tokens = _tokenize_japanese(normalized)
    elif language == "ko":
        tokens = _tokenize_korean(normalized)
    else:
        tokens = _tokenize_default(normalized)

    # Remove stop words
    stop_words = _STOP_WORDS_BY_LANG.get(language, STOP_WORDS_EN)
    filtered = [t for t in tokens if t not in stop_words]

    # Validate keywords
    keywords = _validate_keywords(filtered, language)

    return PreprocessedQuery(
        original=text,
        normalized=normalized,
        language=language,
        keywords=keywords,
        is_cjk=is_cjk,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Normalize query text: strip, collapse whitespace, lowercase."""
    # Unicode NFC normalization for consistent representation
    text = unicodedata.normalize("NFC", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text.strip())
    # Strip leading/trailing punctuation
    text = text.strip(".,;:!?\"'()[]{}<>")
    # Lowercase
    return text.lower()


def _detect_language(text: str) -> str:
    """Detect language of text using langdetect.

    Falls back to "en" for short text or detection failures.
    Deferred import to avoid startup cost.
    """
    if len(text) < MIN_DETECT_LENGTH:
        # Check for CJK characters even in short text
        if _CJK_UNIFIED.search(text):
            return "zh"
        if _HIRAGANA.search(text) or _KATAKANA.search(text):
            return "ja"
        if _HANGUL.search(text):
            return "ko"
        return "en"

    try:
        from langdetect import LangDetectException, detect

        lang: str = detect(text)
        # Normalize variants
        if lang.startswith("zh"):
            return "zh"
        return lang
    except (LangDetectException, Exception):
        logger.debug("Language detection failed for query, defaulting to 'en'")
        return "en"


def _tokenize_default(text: str) -> list[str]:
    """Standard tokenization for Latin-script languages."""
    return tokens(text)


def _tokenize_chinese(text: str) -> list[str]:
    """Chinese tokenization: character bigrams.

    No external word segmenter — bigrams capture most compound words.
    Also extracts any embedded Latin tokens.
    """
    tokens: list[str] = []

    # Extract CJK characters
    cjk_chars = _CJK_UNIFIED.findall(text)
    # Generate bigrams
    for i in range(len(cjk_chars) - 1):
        tokens.append(cjk_chars[i] + cjk_chars[i + 1])
    # Include individual characters as well
    tokens.extend(cjk_chars)

    # Also extract any embedded Latin/number tokens
    latin_tokens = re.findall(r"[a-z0-9_]+", text)
    tokens.extend(latin_tokens)

    return tokens


def _tokenize_japanese(text: str) -> list[str]:
    """Japanese script-aware tokenization.

    Splits into script blocks (kanji, kana, ASCII), then:
    - Kanji blocks get bigrams (like Chinese)
    - Kana blocks kept as-is
    - ASCII blocks tokenized normally
    """
    tokens: list[str] = []

    # Extract kanji characters for bigrams
    kanji_chars = _CJK_UNIFIED.findall(text)
    for i in range(len(kanji_chars) - 1):
        tokens.append(kanji_chars[i] + kanji_chars[i + 1])
    tokens.extend(kanji_chars)

    # Extract kana sequences
    kana_pattern = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]+")
    kana_matches = kana_pattern.findall(text)
    tokens.extend(kana_matches)

    # Extract ASCII tokens
    ascii_tokens = re.findall(r"[a-z0-9_]+", text)
    tokens.extend(ascii_tokens)

    return tokens


def _tokenize_korean(text: str) -> list[str]:
    """Korean tokenization with trailing particle stripping.

    Strips 25 trailing particles (longest-match-first), then validates
    that the remaining stem is at least 2 syllables.
    """
    # First, extract Hangul and Latin tokens
    raw_tokens = re.findall(r"[\uac00-\ud7af]+|[a-z0-9_]+", text)

    tokens: list[str] = []
    for token in raw_tokens:
        # Check if it's a Hangul token
        if _HANGUL.search(token):
            stripped = _strip_korean_particles(token)
            # Validate minimum stem length (2 syllables = 2 Hangul chars)
            hangul_chars = [c for c in stripped if "\uac00" <= c <= "\ud7af"]
            if len(hangul_chars) >= MIN_KOREAN_STEM_SYLLABLES:
                tokens.append(stripped)
        else:
            tokens.append(token)

    return tokens


def _strip_korean_particles(word: str) -> str:
    """Strip trailing Korean particles from a word.

    Tries longest particles first to avoid partial matches.
    """
    for particle in _KOREAN_PARTICLES:
        if word.endswith(particle) and len(word) > len(particle):
            return word[: -len(particle)]
    return word


def _validate_keywords(tokens: list[str], language: str) -> list[str]:
    """Filter tokens to valid keywords.

    Rules:
    - Latin-script languages: skip tokens < 3 chars
    - Skip pure numeric tokens
    - Skip all-punctuation tokens
    - CJK tokens: keep all (even single chars are meaningful)
    """
    is_latin = language in {"en", "es", "pt"}
    min_length = MIN_LATIN_KEYWORD_LENGTH if is_latin else 1

    valid: list[str] = []
    for token in tokens:
        if _PURE_NUMBER.match(token):
            continue
        if _ALL_PUNCT.match(token):
            continue
        if len(token) < min_length:
            continue
        valid.append(token)

    return valid
