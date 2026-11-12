"""Cheap checks that decide whether a question is worth an LLM call before it is searched.

Query understanding (classification, decomposition, rephrasings) costs one to three LLM
round-trips, seconds on a thinking model, and buys recall only when there is something to
rephrase. "Hi", "thanks" and "refund policy" have nothing to rephrase, and a greeting has
nothing to search for at all, so they skip it. This is a deliberately small heuristic; the
conversation layer will decide this properly and replace it.
"""

import re
import unicodedata
from typing import Final

# At most this many whitespace-separated words, and at most this many characters (so a
# long unspaced string, or a CJK sentence, is not mistaken for "short").
SHORT_QUERY_WORDS: Final = 3
SHORT_QUERY_CHARS: Final = 30

_FILLER = r"(?:there|team|everyone|all|sir|madam|ma'?am|buddy|friend|bhai|ji|so|very|much|a\s+lot|again|today|doing)"

_PHRASE = r"""(?:
        (?:hi+|hello+|hey+|hiya|yo|sup|namaste|namaskar|vanakkam|good\s+(?:morning|afternoon|evening|night))
      | (?:thanks?|thank\s+you|thx|ty|ok(?:ay)?|cool|great|nice|got\s+it|alright|fine)
      | (?:bye|goodbye|see\s+you(?:\s+later)?|take\s+care)
      | (?:how\s+are\s+you|how\s+do\s+you\s+do|what'?s\s+up|how'?s\s+it\s+going)
      | (?:who\s+are\s+you|are\s+you\s+(?:a\s+)?(?:bot|robot|human|real)|what\s+can\s+you\s+do)
      | (?:can|could)\s+you\s+help(?:\s+me)?
    )"""

# Whole-message small talk: one or more greetings, thanks, farewells, "how are you" or
# "who are you" phrases, each optionally followed by filler words. Anchored at both ends,
# so "hello, what is the refund policy" is NOT small talk.
_SMALL_TALK: Final = re.compile(
    rf"^{_PHRASE}(?:\s+{_FILLER})*(?:\s+{_PHRASE}(?:\s+{_FILLER})*)*$",
    re.IGNORECASE | re.VERBOSE,
)


_CURLY_APOSTROPHE = chr(0x2019)
_KEEP = "'" + _CURLY_APOSTROPHE + " "


def _plain(query: str) -> str:
    """Lower-cased, trimmed, with punctuation and emoji dropped and spaces collapsed."""
    kept = [
        ch if (ch.isalnum() or ch in _KEEP or unicodedata.category(ch).startswith("M")) else " "
        for ch in query.strip().lower()
    ]
    return " ".join("".join(kept).replace(_CURLY_APOSTROPHE, "'").split())


def is_small_talk(query: str) -> bool:
    """The whole message is a greeting, thanks, a farewell or a question about the bot."""
    return _SMALL_TALK.fullmatch(_plain(query)) is not None


def is_short_query(query: str) -> bool:
    """At most three words and thirty characters."""
    text = query.strip()
    return len(text.split()) <= SHORT_QUERY_WORDS and len(text) <= SHORT_QUERY_CHARS


def skips_query_understanding(query: str) -> bool:
    """True when rephrasing the question would be wasted: small talk or a very short query."""
    return is_short_query(query) or is_small_talk(query)
