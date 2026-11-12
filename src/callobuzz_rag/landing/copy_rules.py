"""Words the public website must not use (LP1).

The default site and every shipped preset have zero warnings (a test). A deployer's own text is
only warned about in the editor, never refused: it is their website.
"""

import re
from dataclasses import dataclass
from typing import Any, Final

from callobuzz_rag.landing.schema import LandingContent

# Whole words, case-insensitive. Promises we cannot keep, words that are not for a business
# owner.
_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        ("ROI", r"(?<!\w)ROI(?!\w)"),
        ("guaranteed", r"(?<!\w)guarantee(?:s|d)?(?!\w)"),
        ("100% accurate", r"(?<!\w)100\s?%\s*accurate(?!\w)"),
        ("never wrong", r"(?<!\w)never\s+wrong(?!\w)"),
        ("corpus", r"(?<!\w)corpus(?!\w)"),
        ("tenant", r"(?<!\w)tenants?(?!\w)"),
        ("ledger", r"(?<!\w)ledger(?!\w)"),
    )
)
_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")


@dataclass(frozen=True)
class CopyWarning:
    path: str
    word: str
    sentence: str


def copy_warnings_in_text(text: str) -> list[str]:
    """The flagged words found in one piece of text (each once, in the order of the rules)."""
    return [name for name, pattern in _PATTERNS if pattern.search(text)]


def _sentence_with(text: str, pattern: re.Pattern[str]) -> str:
    for sentence in _SENTENCE_END.split(text):
        if pattern.search(sentence):
            return sentence.strip()
    return text.strip()


def _strings(data: Any, path: str) -> list[tuple[str, str]]:
    if isinstance(data, str):
        return [(path, data)]
    out: list[tuple[str, str]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            out += _strings(value, f"{path}.{key}" if path else str(key))
    elif isinstance(data, list):
        for index, item in enumerate(data):
            if isinstance(item, dict) and isinstance(item.get("id"), str) and "type" in item:
                label = f"{path}.{item['id']}"
                rest = {k: v for k, v in item.items() if k not in ("id", "type")}
                out += _strings(rest, label)
            else:
                out += _strings(item, f"{path}[{index}]")
    return out


def copy_warnings(content: LandingContent) -> list[CopyWarning]:
    """Every flagged word in every text of the site, in every language, with where it is."""
    dump = content.model_dump(mode="json")
    dump.pop("theme", None)
    warnings: list[CopyWarning] = []
    for path, text in _strings(dump, ""):
        for name, pattern in _PATTERNS:
            if pattern.search(text):
                warnings.append(CopyWarning(path, name, _sentence_with(text, pattern)))
    return warnings
