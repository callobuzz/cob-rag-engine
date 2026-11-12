"""Suggested first questions, from the pages the assistant learned (BIZ4).

Deterministic and instant: no model, no cost. The pages are the ones the ranking already put
first (`rank_score`); a pricing page becomes "What are your prices?", a contact page "Where are
you located?", a page under /treatments/ "Tell me about <its title>". The business card's
opening hours, when it has them, come last. English and Hinglish (Roman Hindi) only.
"""

import re
from collections.abc import Sequence
from typing import Final, Literal
from urllib.parse import urlsplit

Lang = Literal["en", "hi-Latn"]

MAX_SUGGESTIONS: Final = 6
MAX_TITLE: Final = 60
MIN_RANK: Final = 1  # a page ranked at or below zero (archives, old posts) asks nothing

TEXTS: Final[dict[str, dict[str, str]]] = {
    "en": {
        "prices": "What are your prices?",
        "location": "Where are you located?",
        "hours": "What are your opening hours?",
        "booking": "How can I book an appointment?",
        "refund": "What is your refund policy?",
        "delivery": "Do you deliver, and how long does it take?",
        "services": "What services do you offer?",
        "detail": "Tell me about {title}",
    },
    "hi-Latn": {
        "prices": "Aapki fees kitni hai?",
        "location": "Aapka address kya hai?",
        "hours": "Aapke khulne ka time kya hai?",
        "booking": "Appointment kaise book karein?",
        "refund": "Aapki refund policy kya hai?",
        "delivery": "Delivery kitne din mein hoti hai?",
        "services": "Aap kaun si services dete hain?",
        "detail": "{title} ke baare mein batayein",
    },
}
GENERIC: Final[dict[str, tuple[str, ...]]] = {
    lang: (t["services"], t["prices"], t["location"]) for lang, t in TEXTS.items()
}

_KINDS: Final[tuple[tuple[str, frozenset[str]], ...]] = (
    (
        "prices",
        frozenset({"pricing", "price", "prices", "fees", "fee", "charges", "rates", "cost"}),
    ),
    (
        "location",
        frozenset({"contact", "location", "locations", "address", "branches", "directions"}),
    ),
    ("hours", frozenset({"hours", "timings", "timing"})),
    ("booking", frozenset({"book", "booking", "appointment", "appointments"})),
    ("refund", frozenset({"refund", "refunds", "returns", "cancellation"})),
    ("delivery", frozenset({"shipping", "delivery"})),
)
_INDEX: Final = frozenset(
    {"services", "service", "treatments", "products", "packages", "menu", "courses"}
)
_DETAIL_PARENTS: Final = frozenset(
    {
        "services",
        "service",
        "treatments",
        "treatment",
        "products",
        "product",
        "packages",
        "package",
        "courses",
        "course",
        "menu",
        "doctors",
    }
)
_SPLIT = re.compile(r"[^a-z0-9]+")
_SEPARATORS = re.compile(r"\s+[|\-–—:·]+\s+|\s*\|\s*")  # noqa: RUF001 - en and em dashes separate a page title from the site name


def _words(text: str) -> set[str]:
    return {w for w in _SPLIT.split(text.lower()) if w}


def _clean_title(title: str, url: str) -> str:
    """The page's own name: the part before the site name, cut on a word, or the last path
    segment made readable."""
    head = _SEPARATORS.split(title.strip())[0].strip() if title.strip() else ""
    if not head:
        segment = [s for s in urlsplit(url).path.split("/") if s][-1:]
        head = (
            " ".join(w.capitalize() for w in _SPLIT.split(segment[0].lower()) if w)
            if segment
            else ""
        )
    if len(head) > MAX_TITLE:
        cut = head[:MAX_TITLE]
        head = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip()
    return head


def _question(url: str, title: str, texts: dict[str, str]) -> str | None:
    segments = [s for s in urlsplit(url).path.lower().split("/") if s]
    if not segments:
        return None  # the home page says nothing in particular
    path_words = _words(segments[-1])
    for kind, vocabulary in _KINDS:
        if path_words & vocabulary:
            return texts[kind]
    if len(segments) == 1 and path_words & _INDEX:
        return texts["services"]
    if len(segments) >= 2 and _words(segments[-2]) & _DETAIL_PARENTS:
        name = _clean_title(title, url)
        return texts["detail"].format(title=name) if name else None
    title_words = _words(title)
    for kind, vocabulary in _KINDS:
        if title_words & vocabulary and len(segments) == 1:
            return texts[kind]
    return None


def suggest(
    pages: Sequence[tuple[str, str | None, int]], *, card_hours: bool, lang: str = "en"
) -> list[str]:
    """At most six questions for learned pages `(url, title, rank_score)`.

    The best-ranked pages ask first. With no usable page the generic starters stand in; the
    card's opening hours (`card_hours`) always take the last place."""
    key: Lang = "hi-Latn" if lang == "hi-Latn" else "en"
    texts = TEXTS[key]
    out: list[str] = []
    for url, title, score in sorted(pages, key=lambda p: (-p[2], p[0])):
        if score < MIN_RANK:
            continue
        question = _question(url, title or "", texts)
        if question is not None and question not in out:
            out.append(question)
    if not out:
        out = list(GENERIC[key])
    if card_hours:
        out = [q for q in out if q != texts["hours"]][: MAX_SUGGESTIONS - 1]
        out.append(texts["hours"])
    return out[:MAX_SUGGESTIONS]
