"""Shared vocabulary of the conversation layer (README "Labels, routes, channels")."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class TurnLabel(StrEnum):
    SMALLTALK = "smalltalk"
    BUSINESS_QUESTION = "business_question"
    DOMAIN_EXPLAINER = "domain_explainer"
    OFF_TOPIC = "off_topic"
    BUYING_INTENT = "buying_intent"
    HUMAN_REQUEST = "human_request"
    ABUSE = "abuse"
    OTHER = "other"


class Route(StrEnum):
    SMALLTALK = "smalltalk"
    GROUNDED = "grounded"
    VERBATIM = "verbatim"
    EXPLAINER = "explainer"
    DECLINE = "decline"
    NO_ANSWER = "no_answer"
    ABUSE_WARNING = "abuse_warning"
    HANDOFF = "handoff"
    LEAD = "lead"


class Channel(StrEnum):
    API = "api"
    STREAM = "stream"
    WIDGET = "widget"
    CONVERSATION = "conversation"
    PLAYGROUND = "playground"
    # COB, the built-in assistant (COB2)
    COB = "cob"
    # TV6 channel: a customer on the business's WhatsApp number
    WHATSAPP = "whatsapp"


class Script(StrEnum):
    LATIN = "latin"
    DEVANAGARI = "devanagari"
    OTHER = "other"


class OfficeState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


LangKey = Literal["en", "hi", "hi-Latn", "mr", "ta", "te", "bn", "gu", "kn", "ml", "pa"]
LANG_KEYS: tuple[str, ...] = (
    "en",
    "hi",
    "hi-Latn",
    "mr",
    "ta",
    "te",
    "bn",
    "gu",
    "kn",
    "ml",
    "pa",
)


@dataclass(frozen=True)
class LanguageInfo:
    """What `detect_language` saw: the language, its script and how sure it is (0..1)."""

    code: LangKey
    script: Script
    confidence: float


def script_of(code: str) -> Script:
    """The script a reply in `code` is written in."""
    if code in ("en", "hi-Latn"):
        return Script.LATIN
    if code in ("hi", "mr"):
        return Script.DEVANAGARI
    return Script.OTHER
