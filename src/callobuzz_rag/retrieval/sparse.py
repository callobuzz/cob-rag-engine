"""BM25 sparse vectors for the keyword leg of hybrid search.

Documents and queries go through the same analyzer — tokenize, drop stopwords, stem —
so "shipping" in a question meets "shipped" in a chunk. Document terms are weighted
with BM25's saturating, length-normalized term frequency; query terms weigh 1. Qdrant
multiplies in IDF at search time (the collection's sparse vectors use
`Modifier.IDF`, see vectordb/qdrant.py), so a dot product of the two is BM25.

BM25 needs the corpus's average document length. It is fixed
(`RAG_SPARSE_AVG_DOC_TOKENS`) rather than measured, so a vector never depends on
what else is in the collection; on the BEIR datasets 64-128 scored within a
point of the true average.

The previous version stored count / length with no stemming and no saturation. On
BEIR it scored 0.51 nDCG@10 on SciFact and 0.07 on FiQA, against 0.69 and 0.25 now.
Vectors written by it should be rebuilt: `python -m callobuzz_rag.retrieval.reindex`.

Indic scripts: the regex word class splits every Hindi (Bengali, Tamil...) word at its vowel
signs, so the analyzer once indexed fragments. Words are now whole (see `text/tokenize.py`) and Hindi /
Hinglish function words are stop words. Vectors written before that need
`python -m callobuzz_rag.retrieval.reindex --apply`.
"""

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from callobuzz_rag.text.tokenize import tokenize as _tokenize

if TYPE_CHECKING:
    from callobuzz_rag.vectordb.base import SparseVector

# Max index value for sparse vector dimensions (2^31 - 1).
# Qdrant sparse vectors use uint32 indices; we cap at signed int32 max
# to avoid any cross-language interop issues.
SPARSE_INDEX_MAX = 2_147_483_647

# Tokenization is `callobuzz_rag.text.tokenize`: letters, combining marks and digits, so a
# Hindi word is one token (plain `\w` cuts it at every vowel sign).

# Minimum token length to include (skip single chars for Latin scripts).
MIN_TOKEN_LENGTH = 2

# Stemmers that work on a non-Latin script; every other Snowball stemmer is for Latin text and
# must not touch Devanagari, Bengali, Tamil... tokens.
_NON_LATIN_STEMMERS = frozenset({"arabic", "russian"})

# Snowball stemmer name -> stop-word table code in retrieval/preprocessing.py.
_STOPWORD_CODES = {"english": "en", "spanish": "es", "portuguese": "pt", "arabic": "ar"}


@dataclass(frozen=True)
class SparseConfig:
    language: str = "english"  # a Snowball language, or "none" for no stemming/stopwords
    k1: float = 1.2
    b: float = 0.75
    avg_doc_tokens: float = 128.0

    @classmethod
    def from_settings(cls, settings: Any) -> "SparseConfig":
        return cls(
            language=settings.sparse_language,
            k1=settings.sparse_bm25_k1,
            b=settings.sparse_bm25_b,
            avg_doc_tokens=settings.sparse_avg_doc_tokens,
        )


@lru_cache(maxsize=1)
def _default_config() -> SparseConfig:
    from callobuzz_rag.core.config import get_settings

    return SparseConfig.from_settings(get_settings())


@lru_cache(maxsize=8)
def _analyzer(language: str) -> tuple[frozenset[str], Any]:
    """(stop words, stem function or None) for a Snowball language name."""
    language = language.lower()
    if language in ("", "none"):
        return frozenset(), None
    from nltk.stem.snowball import SnowballStemmer

    from callobuzz_rag.retrieval.preprocessing import _STOP_WORDS_BY_LANG
    from callobuzz_rag.text.stopwords import STOPWORDS_INDIC_AND_HINGLISH

    if language not in SnowballStemmer.languages:
        raise ValueError(
            f"RAG_SPARSE_LANGUAGE={language!r} is not a Snowball language; use one of "
            f"{', '.join(SnowballStemmer.languages)} or 'none'"
        )
    # Hindi and romanized-Hindi function words are dropped whatever the stemmer language:
    # Hinglish is typed next to English everywhere in India.
    stop = (
        _STOP_WORDS_BY_LANG.get(_STOPWORD_CODES.get(language, ""), frozenset())
        | STOPWORDS_INDIC_AND_HINGLISH
    )
    stem = SnowballStemmer(language).stem
    if language in _NON_LATIN_STEMMERS:
        return stop, stem

    def stem_latin(term: str) -> str:
        # A Latin-script stemmer on an Indic token only risks mangling it.
        return stem(term) if term.isascii() or _is_latin(term) else term

    return stop, stem_latin


def _is_latin(term: str) -> bool:
    """Whether every letter of `term` is Latin (accented ones included)."""
    return all(ord(c) < 0x250 or 0x300 <= ord(c) <= 0x36F or c.isdigit() for c in term)


def tokenize(text: str) -> list[str]:
    """Lowercased, NFC word tokens, duplicates kept (no stemming, no stopword removal)."""
    return _tokenize(text, MIN_TOKEN_LENGTH)


def analyze(text: str, config: SparseConfig | None = None) -> list[str]:
    """Index/query terms: tokens minus stopwords, stemmed. Identifiers survive intact —
    a token with a digit is never a stopword and Snowball leaves it unchanged."""
    stop, stem = _analyzer((config or _default_config()).language)
    terms = [t for t in tokenize(text) if t not in stop]
    return [stem(t) for t in terms] if stem else terms


def token_to_index(token: str) -> int:
    """Map a term to a stable integer index (first 4 bytes of its MD5)."""
    digest = hashlib.md5(token.encode("utf-8")).digest()
    raw = int.from_bytes(digest[:4], byteorder="big", signed=False)
    return raw % (SPARSE_INDEX_MAX + 1)


class SparseVectorData:
    """Sparse vector representation with indices and values.

    Matches the shape expected by qdrant_client.models.SparseVector.
    """

    __slots__ = ("indices", "values")

    def __init__(self, indices: list[int], values: list[float]) -> None:
        self.indices = indices
        self.values = values

    def is_empty(self) -> bool:
        """Return True if the vector has no non-zero dimensions."""
        return len(self.indices) == 0

    def to_sparse_vector(self) -> "SparseVector":
        """Convert to vectordb SparseVector for Qdrant upsert/search."""
        from callobuzz_rag.vectordb.base import SparseVector as _SparseVector

        return _SparseVector(indices=self.indices, values=self.values)


def _to_vector(weights: dict[str, float]) -> SparseVectorData:
    # Two terms hashing to one index (rare) share it; their weights add.
    index_values: dict[int, float] = {}
    for term, weight in weights.items():
        idx = token_to_index(term)
        index_values[idx] = index_values.get(idx, 0.0) + weight
    ordered = sorted(index_values.items())
    return SparseVectorData(indices=[i for i, _ in ordered], values=[v for _, v in ordered])


def text_to_sparse_vector(text: str, config: SparseConfig | None = None) -> SparseVectorData:
    """Document-side vector: BM25 term-frequency weight per term."""
    config = config or _default_config()
    terms = analyze(text, config)
    if not terms:
        return SparseVectorData(indices=[], values=[])
    counts: dict[str, int] = {}
    for term in terms:
        counts[term] = counts.get(term, 0) + 1
    norm = config.k1 * (1 - config.b + config.b * len(terms) / config.avg_doc_tokens)
    return _to_vector({t: c * (config.k1 + 1) / (c + norm) for t, c in counts.items()})


def query_to_sparse_vector(text: str, config: SparseConfig | None = None) -> SparseVectorData:
    """Query-side vector: weight 1 per distinct term, so the score is BM25."""
    return _to_vector(dict.fromkeys(analyze(text, config), 1.0))
