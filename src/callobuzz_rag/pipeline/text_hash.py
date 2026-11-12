"""The one text normalisation behind every duplicate check.

Two places hash text and must agree: the paragraph hashes that find page furniture
across documents (`pipeline/boilerplate.py`) and the chunk hashes that let search show
a passage stored in several documents once (`chunks.text_hash`, the `text_hash` payload
field). Both go through `text_hash`, so a paragraph that is a chunk on its own hashes
the same either way.

Normalisation: Unicode NFKC (ligatures, full-width letters), case-folded, runs of
whitespace collapsed to one space. Digits and punctuation stay: "5 years" and
"10 years" are different passages.
"""

import hashlib
import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")

HASH_LENGTH = 16


def normalize_text(text: str) -> str:
    """`text` as compared for duplicates: NFKC, case-folded, whitespace collapsed."""
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", text).casefold()).strip()


def text_hash(text: str) -> str:
    """The first 16 hex characters of the SHA-256 of the normalised text."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()[:HASH_LENGTH]
