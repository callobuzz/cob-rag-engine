# src/callobuzz_rag/conversation/rules.py
"""The deterministic first pass over a visitor's message (design §3 step 3).

Rules come from data files so a new phrasing is a data change. Small talk must be the
WHOLE message ("hi" yes, "hi, what are your timings?" no — the greeting is stripped and
the rest goes to the model), and so must a bare acknowledgement ("ok", "haan ji": label
`other`); human requests, abuse and buying intent are searched.
"""

import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from callobuzz_rag.conversation.safety import abuse_level
from callobuzz_rag.conversation.types import TurnLabel
from callobuzz_rag.text.tokenize import AFTER_WORD, WORD_CLASS

RULES = Path(__file__).parent / "data" / "rules"
_LANGS = ("en", "hi", "hi-Latn")
_SMALLTALK_INTENTS = (
    "greeting",
    "thanks",
    "bye",
    "how_are_you",
    "who_are_you",
    "are_you_bot",
)
# When a message holds several phrases ("namaste, kaise ho?") the most specific one names
# the reply: a question about the assistant outranks a greeting.
_INTENT_RANK = {intent: rank for rank, intent in enumerate(_SMALLTALK_INTENTS)}
_PUNCT = re.compile(rf"[^{WORD_CLASS}\s']+")
_HONORIFICS = "ji|जी|sir|madam|maam|bhai|didi|please|pls|plz"
_TRAILING_HONORIFIC = re.compile(rf"(?: (?:{_HONORIFICS}))+$")
# Words that may sit between two small-talk phrases without being a question of their own
# (the "filler" list in the data file joins these).
_CONNECTORS = ("and", "aur", "toh", "और", "तो")


def normalize_message(text: str) -> str:
    lowered = text.lower().replace("।", " ")
    no_punct = _PUNCT.sub(" ", lowered)
    return re.sub(r"\s+", " ", no_punct).strip()


# A human-request phrase is a gate bypass (TV1), so a negation or a question about the business's
# own services ("don't call me", "do you offer chat with a doctor") must not trigger it.
_NEGATION_BEFORE = re.compile(
    r"(?:^|\s)(?:don't|dont|do not|never|mat|मत)\s+"
    r"(?:(?:want|wish|need|like|have|ever|even|you|please|just|kindly|try|to|ask|wanna)\s+){0,3}$"
)
_NEGATION_AFTER = re.compile(r"^\s*(?:\S+\s+)?(?:nahi|nahin|nhi|नहीं|mat|मत)(?:\s|$)")
_SERVICE_QUESTION_BEFORE = re.compile(
    r"(?:^|\s)(?:do|does|did) (?:you|u|they|your \w+) (?:offer|have|provide|support|allow|give)"
)


def _human_request_refused(norm: str, found: re.Match[str]) -> bool:
    before = norm[: found.start()] + " "
    after = norm[found.end() :]
    return bool(
        _NEGATION_BEFORE.search(before)
        or _NEGATION_AFTER.match(after)
        or _SERVICE_QUESTION_BEFORE.search(before)
    )


@dataclass(frozen=True)
class RuleOutcome:
    label: TurnLabel | None
    intent: str | None
    lang: str | None
    remainder: str
    greeted: bool


def _load(name: str) -> dict[str, dict[str, list[str]]]:
    raw = json.loads((RULES / name).read_text("utf-8"))
    raw.pop("version", None)
    return raw  # type: ignore[no-any-return]


class RuleLabeler:
    def __init__(self) -> None:
        small = _load("smalltalk.json")
        self._smalltalk: list[tuple[str, str, re.Pattern[str]]] = [
            (intent, lang, re.compile(rf"^(?:{frag})$"))
            for intent in _SMALLTALK_INTENTS
            for lang in _LANGS
            for frag in small[intent].get(lang, [])
        ]
        self._smalltalk_prefix: list[tuple[str, str, re.Pattern[str]]] = [
            (intent, lang, re.compile(rf"^(?:{frag})(?=\s|$)\s*"))
            for intent in _SMALLTALK_INTENTS
            for lang in _LANGS
            for frag in small[intent].get(lang, [])
        ]
        words = [
            *_HONORIFICS.split("|"),
            *_CONNECTORS,
            *(f for lang in _LANGS for f in small.get("filler", {}).get(lang, [])),
        ]
        self._connector = re.compile(
            rf"^(?:{'|'.join(sorted(words, key=len, reverse=True))})(?=\s|$)\s*"
        )
        self._greeting_prefix = re.compile(
            r"^(?:"
            + "|".join(f for lang in _LANGS for f in small["greeting"].get(lang, []))
            + rf"){AFTER_WORD}[ ,]*"
        )
        # A bare acknowledgement ("ok", "hmm", "haan ji", "नहीं") is `other`, never small talk:
        # it may answer the assistant's last offer, so it is not given a canned reply.
        self._ack: list[tuple[str, re.Pattern[str]]] = []
        for lang in _LANGS:
            words = sorted(
                [*small["acknowledgement"].get(lang, []), *_HONORIFICS.split("|")],
                key=len,
                reverse=True,
            )
            self._ack.append((lang, re.compile(rf"^(?:(?:{'|'.join(words)})(?: |$))+$")))
        self._human = self._searched(_load("human_request.json")["human_request"])
        self._buying = self._searched(_load("buying_intent.json")["buying_intent"])
        self._contact = self._searched(_load("contact_request.json")["contact_request"])

    def asked_for_contact(self, text: str) -> bool:
        norm = normalize_message(text)
        return any(pattern.search(norm) for _lang, pattern in self._contact)

    @staticmethod
    def _searched(table: dict[str, list[str]]) -> list[tuple[str, re.Pattern[str]]]:
        return [
            (lang, re.compile(rf"(?:^|\s)(?:{frag})(?:\s|$)"))
            for lang in _LANGS
            for frag in table.get(lang, [])
        ]

    def _only_smalltalk(self, norm: str) -> tuple[str, str] | None:
        """(intent, language) when the WHOLE message is small talk, however many phrases it
        strings together ("namaste, kaise ho?" = greeting + how are you). One word of a real
        question anywhere and it is not small talk: the rest goes to the model."""
        rest = norm
        found: list[tuple[str, str]] = []
        while rest:
            best: tuple[str, str, int] | None = None
            for intent, lang, pattern in self._smalltalk_prefix:
                m = pattern.match(rest)
                if m is not None and (best is None or m.end() > best[2]):
                    best = (intent, lang, m.end())
            if best is None:
                connector = self._connector.match(rest)
                if connector is None:
                    return None
                rest = rest[connector.end() :]
                continue
            found.append((best[0], best[1]))
            rest = rest[best[2] :]
        if not found:
            return None
        return max(found, key=lambda f: _INTENT_RANK[f[0]])

    def classify(self, text: str) -> RuleOutcome:
        norm = normalize_message(text)
        if abuse_level(text) > 0:
            return RuleOutcome(TurnLabel.ABUSE, "abuse", None, norm, False)
        for lang, pattern in self._human:
            found = pattern.search(norm)
            if found is not None and not _human_request_refused(norm, found):
                return RuleOutcome(TurnLabel.HUMAN_REQUEST, "human_request", lang, norm, False)
        bare = _TRAILING_HONORIFIC.sub("", norm)
        for intent, lang, pattern in self._smalltalk:
            if pattern.match(norm) or pattern.match(bare):
                return RuleOutcome(TurnLabel.SMALLTALK, intent, lang, "", False)
        only = self._only_smalltalk(norm)
        if only is not None:
            return RuleOutcome(TurnLabel.SMALLTALK, only[0], only[1], "", False)
        for lang, pattern in self._ack:
            if norm and pattern.match(norm):
                return RuleOutcome(TurnLabel.OTHER, "acknowledgement", lang, norm, False)
        greeted = False
        remainder = norm
        stripped = self._greeting_prefix.sub("", norm, count=1)
        if stripped != norm and stripped:
            greeted, remainder = True, stripped
        for lang, pattern in self._buying:
            if pattern.search(remainder):
                return RuleOutcome(
                    TurnLabel.BUYING_INTENT, "buying_intent", lang, remainder, greeted
                )
        return RuleOutcome(None, None, None, remainder, greeted)


@cache
def rule_labeler() -> RuleLabeler:
    return RuleLabeler()
