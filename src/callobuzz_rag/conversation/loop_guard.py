"""Notice when the assistant repeats itself (TV1). Pure.

A reply is fingerprinted by its route and its normalised text. Two replies in a row with the same
fingerprint (small talk never counts) mean the visitor is going round in a circle: the next turn
is the handoff card (reason `loop`), and the counters start again.
"""

import hashlib
import unicodedata
from typing import TYPE_CHECKING

from callobuzz_rag.conversation.types import Route

if TYPE_CHECKING:
    from callobuzz_rag.conversation.state import ConversationState

# Routes that never count: a greeting repeats legitimately, and a card or lead reply is the exit.
_IGNORED = frozenset({Route.SMALLTALK, Route.HANDOFF, Route.LEAD})
_TEXT_CHARS = 500
LOOP_AT = 2


def _normalise(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def fingerprint(route: Route, text: str) -> str:
    """16 hex characters: `sha256(route + normalised text[:500])`."""
    body = _normalise(text)[:_TEXT_CHARS]
    return hashlib.sha256(f"{route.value}{body}".encode()).hexdigest()[:16]


def note_reply(state: "ConversationState", route: Route, text: str) -> None:
    """Record what the assistant just said."""
    if route in _IGNORED:
        return
    fp = fingerprint(route, text)
    if state.reply_fp == fp:
        state.repeat_count += 1
    else:
        state.reply_fp = fp
        state.repeat_count = 1


def loop_detected(state: "ConversationState") -> bool:
    return state.repeat_count >= LOOP_AT


def reset(state: "ConversationState") -> None:
    state.reply_fp = None
    state.repeat_count = 0
