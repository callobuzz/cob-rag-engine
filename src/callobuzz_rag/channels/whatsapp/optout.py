"""STOP and START on WhatsApp: rules only, never a model.

Meta's policy says to honour a request to stop messages in any wording. A message is an opt-out
only when the *whole* message is the request ("STOP", "band karo", "please stop messaging me");
a question that merely contains the word ("stop bleeding after extraction?") is answered.
"""

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Literal

_RULES = Path(__file__).resolve().parents[2] / "conversation" / "data" / "rules" / "opt_out.json"
Intent = Literal["stop", "start"]

_PUNCT = re.compile(
    '[!.,;:?"\N{LEFT DOUBLE QUOTATION MARK}\N{RIGHT DOUBLE QUOTATION MARK}\N{DEVANAGARI DANDA}]+'
)
# Emoji and invisible characters (symbols, modifiers, format marks incl. variation selectors).
# Category Mn is kept: it holds the Devanagari vowel signs.
_DROP_CATEGORIES = frozenset({"So", "Sk", "Cf"})
_DROP_CHARS = "\ufe0e\ufe0f"
_SPACES = re.compile(r"\s+")


@lru_cache(maxsize=1)
def _rules() -> dict[str, list[re.Pattern[str]]]:
    raw = json.loads(_RULES.read_text(encoding="utf-8"))
    return {key: [re.compile(p, re.IGNORECASE) for p in raw[key]] for key in ("stop", "start")}


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).strip().lower()
    text = text.replace("\N{RIGHT SINGLE QUOTATION MARK}", "'")
    text = text.replace("\N{LEFT SINGLE QUOTATION MARK}", "'")
    text = "".join(
        ch
        for ch in text
        if unicodedata.category(ch) not in _DROP_CATEGORIES and ch not in _DROP_CHARS
    )
    text = _PUNCT.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def opt_out_intent(text: str) -> Intent | None:
    """`"stop"`, `"start"`, or None when the message is anything else."""
    norm = _normalise(text)
    if not norm or len(norm) > 60:
        return None
    rules = _rules()
    if any(p.search(norm) for p in rules["stop"]):
        return "stop"
    if any(p.search(norm) for p in rules["start"]):
        return "start"
    return None
