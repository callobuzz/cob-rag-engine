r"""The one word tokenizer: letters, combining marks and digits make a word.

Python's `\w` matches letters, digits and the underscore but NOT combining marks
(Unicode Mn/Mc/Me). Every Indic script writes its vowel signs, virama (halant), nukta and
anusvara as marks, so `re.findall(r"\w+", "होम कलेक्शन की कीमत")` shatters the words at each
sign: 6 words become 11 fragments, and BM25, the refusal and source-chip word overlap, MMR
and the rest all stop working for Hindi and every other Indic language. Thai, Burmese,
Khmer, Arabic with harakat and Hebrew with niqqud break the same way.

This module builds the missing class from `unicodedata` (so it follows the interpreter's
Unicode version) and exposes it as:

- `tokenize` / `tokens` for splitting text into words;
- `WORD_CLASS` and `word_pattern` for regexes that need a word boundary, since `\b` and
  `(?<!\w)` see a combining mark as "not a word character" and cut Indic words in half;
- `normalize` for the form every comparison should start from (NFC, lower case).

ZWJ and ZWNJ (U+200D, U+200C) stay inside a word, so "क्‍ष" and Persian "می‌خواهم" are not
split; `tokenize` then drops them, so a word typed with and without them is one term.
"""

import re
import sys
import unicodedata
from collections.abc import Iterable
from functools import lru_cache

ZERO_WIDTH_JOINERS = chr(0x200C) + chr(0x200D)  # ZWNJ, ZWJ
_JOINER_TABLE = {ord(c): None for c in ZERO_WIDTH_JOINERS}

# Mark categories: nonspacing, spacing combining, enclosing.
_MARK_CATEGORIES = frozenset({"Mn", "Mc", "Me"})
# Planes 0-2 hold every script with marks that matter; above that is tags and variation
# selectors, which are not word material.
_SCAN_LIMIT = min(sys.maxunicode, 0x2FFFF) + 1


def _mark_ranges() -> str:
    """Character-class text spelling every Mn/Mc/Me code point as ranges."""
    out: list[str] = []
    start: int | None = None
    category = unicodedata.category
    for cp in range(0x300, _SCAN_LIMIT):
        if category(chr(cp)) in _MARK_CATEGORIES:
            if start is None:
                start = cp
        elif start is not None:
            out.append(_span(start, cp - 1))
            start = None
    if start is not None:
        out.append(_span(start, _SCAN_LIMIT - 1))
    return "".join(out)


def _span(lo: int, hi: int) -> str:
    # Marks are never class metacharacters, so the literal characters are safe.
    return chr(lo) if lo == hi else f"{chr(lo)}-{chr(hi)}"


_MARKS = _mark_ranges()

# Body of a character class matching anything that can be inside a word.
WORD_CLASS = rf"\w{_MARKS}{ZERO_WIDTH_JOINERS}"

# A word starts with a letter or digit (a stray mark or joiner never opens one) and goes on
# through letters, digits, marks and joiners.
_WORD = re.compile(rf"\w[{WORD_CLASS}]*")
_ASCII_WORD = re.compile(r"\w+")
BEFORE_WORD = rf"(?<![{WORD_CLASS}])"
AFTER_WORD = rf"(?![{WORD_CLASS}])"


def normalize(text: str) -> str:
    """NFC, then lower case. Pure ASCII skips the normalisation. Decomposed and precomposed
    spellings of one word (a nukta typed as one character or as base + sign) compare equal."""
    if text.isascii():
        return text.lower()
    lowered = unicodedata.normalize("NFC", text).lower()
    # `lower()` can emit a combining mark (dotted I); compose it again.
    return unicodedata.normalize("NFC", lowered) if not lowered.isascii() else lowered


def tokens(text: str, min_length: int = 1) -> list[str]:
    """Words of `text` as written (no case folding), duplicates kept, joiners removed."""
    if text.isascii():
        # No marks or joiners in ASCII: plain `\w+` is the same split, and faster.
        found = _ASCII_WORD.findall(text)
    else:
        found = _WORD.findall(text)
        found = [w.translate(_JOINER_TABLE) if _has_joiner(w) else w for w in found]
    return [w for w in found if len(w) >= min_length] if min_length > 1 else found


def tokenize(text: str, min_length: int = 1) -> list[str]:
    """Lower-cased, NFC-normalised words of `text`, duplicates kept. A word is made of
    letters, combining marks and digits, so Indic words stay whole."""
    return tokens(normalize(text), min_length)


def word_set(text: str, min_length: int = 1) -> set[str]:
    """Distinct `tokenize` words."""
    return set(tokenize(text, min_length))


def _has_joiner(word: str) -> bool:
    return any(c in word for c in ZERO_WIDTH_JOINERS)


@lru_cache(maxsize=512)
def word_pattern(term: str, flags: int = 0, *, escape: bool = True) -> "re.Pattern[str]":
    """Regex matching `term` as a whole word, with boundaries that understand combining
    marks (`\b` does not: it ends a Hindi word before its last vowel sign)."""
    body = re.escape(term) if escape else term
    return re.compile(rf"{BEFORE_WORD}(?:{body}){AFTER_WORD}", flags)


def is_wordy(text: str) -> bool:
    """Whether `text` contains at least one word character."""
    return _WORD.search(text) is not None


def join_alternatives(terms: Iterable[str]) -> str:
    """Longest-first alternation of escaped `terms`, for building a `word_pattern`."""
    return "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))


__all__ = [
    "AFTER_WORD",
    "BEFORE_WORD",
    "WORD_CLASS",
    "ZERO_WIDTH_JOINERS",
    "is_wordy",
    "join_alternatives",
    "normalize",
    "tokenize",
    "tokens",
    "word_pattern",
    "word_set",
]
