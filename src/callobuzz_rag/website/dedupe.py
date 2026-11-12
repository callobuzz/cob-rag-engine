"""Near-duplicate pages (BIZ1): an exact text hash, then a 64-bit SimHash.

SimHash over word 3-shingles; two pages are near-duplicates when the Hamming distance of their
hashes is at most 3. Four 16-bit bands index the hashes: two hashes that differ in at most three
bits must agree on at least one whole band (pigeonhole), so a lookup compares only the pages
that share a band, never all pairs. Pages are fed in rank order, so the better-ranked page is
the one that is kept and the later one is skipped as its duplicate.
"""

import hashlib
import string
from collections.abc import Hashable
from typing import Final

HAMMING_MAX: Final = 3
_BANDS: Final = 4
_PUNCT: Final = string.punctuation + "।॥•–—‘’“”"  # noqa: RUF001


def _tokens(text: str) -> list[str]:
    out = []
    for raw in text.lower().split():
        word = raw.strip(_PUNCT)
        if word:
            out.append(word)
    return out


def text_hash(text: str) -> str:
    """Hash of the normalised text (lower case, whitespace and edge punctuation ignored)."""
    return hashlib.sha256(" ".join(_tokens(text)).encode("utf-8")).hexdigest()


def _h64(value: str) -> int:
    return int.from_bytes(hashlib.blake2b(value.encode("utf-8"), digest_size=8).digest(), "big")


def simhash(text: str) -> int:
    """The 64-bit SimHash (unsigned) of `text` over word 3-shingles."""
    words = _tokens(text)
    if not words:
        return 0
    shingles = (
        [" ".join(words[i : i + 3]) for i in range(len(words) - 2)] if len(words) >= 3 else words
    )
    votes = [0] * 64
    for shingle in shingles:
        h = _h64(shingle)
        for bit in range(64):
            votes[bit] += 1 if (h >> bit) & 1 else -1
    result = 0
    for bit in range(64):
        if votes[bit] > 0:
            result |= 1 << bit
    return result


def to_db(value: int) -> int:
    """Unsigned 64-bit -> the signed BigInteger the column holds."""
    return value - (1 << 64) if value >= 1 << 63 else value


def from_db(value: int) -> int:
    return value + (1 << 64) if value < 0 else value


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


class NearDuplicateIndex:
    """`find` returns the key of an earlier page that `text` duplicates, else None."""

    def __init__(self) -> None:
        self._exact: dict[str, Hashable] = {}
        self._bands: list[dict[int, list[tuple[Hashable, int]]]] = [{} for _ in range(_BANDS)]
        self.comparisons = 0  # exposed for the "no all-pairs scan" test

    def find(self, text: str) -> tuple[Hashable, str] | None:
        """(key, "exact" | "near") of the earlier duplicate, or None."""
        digest = text_hash(text)
        if digest in self._exact:
            return self._exact[digest], "exact"
        sim = simhash(text)
        seen: set[Hashable] = set()
        for band, table in enumerate(self._bands):
            for key, other in table.get((sim >> (16 * band)) & 0xFFFF, ()):
                if key in seen:
                    continue
                seen.add(key)
                self.comparisons += 1
                if hamming(sim, other) <= HAMMING_MAX:
                    return key, "near"
        return None

    def add(self, key: Hashable, text: str) -> None:
        self._exact.setdefault(text_hash(text), key)
        self.add_hash(key, simhash(text))

    def add_hash(self, key: Hashable, sim: int) -> None:
        """Index a page by its stored SimHash (unsigned), without its text: what a single-page
        run knows about the pages the site already holds. Exact copies are found by their
        Hamming distance of 0."""
        for band, table in enumerate(self._bands):
            table.setdefault((sim >> (16 * band)) & 0xFFFF, []).append((key, sim))
