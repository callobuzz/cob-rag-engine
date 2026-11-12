# src/callobuzz_rag/conversation/safety.py
"""Abuse, prompt-injection markers and system-prompt echo (design §5.4, §5.8)."""

import re
from collections.abc import Iterable
from functools import cache
from pathlib import Path

from callobuzz_rag.retrieval.safety import (
    detect_suspicious_patterns,
    fold_homoglyphs,
    sanitize_for_prompt_literal,
)
from callobuzz_rag.text.tokenize import word_pattern

DATA = Path(__file__).parent / "data" / "profanity"


@cache
def _profanity_patterns() -> tuple[re.Pattern[str], ...]:
    patterns: list[re.Pattern[str]] = []
    for name in ("en.txt", "hi.txt", "hi-Latn.txt"):
        for line in (DATA / name).read_text("utf-8").splitlines():
            term = line.strip()
            if not term or term.startswith("#"):
                continue
            # Word boundaries that also work for Indic scripts (\b does not).
            patterns.append(word_pattern(term, re.IGNORECASE))
    return tuple(patterns)


def abuse_level(text: str) -> int:
    """0 = clean, 1 = one abusive term, 2 = two or more."""
    folded = fold_homoglyphs(text)
    hits = sum(len(p.findall(folded)) for p in _profanity_patterns())
    return min(hits, 2)


def injection_markers(text: str) -> list[str]:
    """Known prompt-injection phrasings in a visitor's message (log and trace only)."""
    return detect_suspicious_patterns(sanitize_for_prompt_literal(fold_homoglyphs(text)))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


class EchoFilter:
    """True when an output sentence repeats ≥ `window` characters of the system prompt.

    `allowed` are the parts of the prompt the assistant is told to say (its disclosure line
    "I'm Asha, the AI assistant of ...", the business card, the topics it helps with, the
    disclaimer): they are cut out of the prompt before the windows are taken, so saying them
    is not an echo, while every instruction around them still is."""

    def __init__(self, system_prompt: str, window: int = 40, allowed: Iterable[str] = ()) -> None:
        self._window = window
        norm = _norm(system_prompt)
        for text in sorted({_norm(a) for a in allowed if a.strip()}, key=len, reverse=True):
            norm = norm.replace(text, "\0")
        self._spans = {
            part[i : i + window]
            for part in norm.split("\0")
            for i in range(0, max(0, len(part) - window + 1))
        }

    def leaks(self, sentence: str) -> bool:
        norm = _norm(sentence)
        w = self._window
        return any(norm[i : i + w] in self._spans for i in range(0, max(0, len(norm) - w + 1)))
