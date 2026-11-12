# src/callobuzz_rag/conversation/pii.py
"""Personal identifiers a visitor should never have typed (design §5.7).

Detected by pattern + checksum and replaced with a short marker before the text is
labelled, searched, sent to any model, stored or logged. Phone numbers and e-mail
addresses are NOT masked here: a visitor shares them on purpose to be contacted, and the
lead form (C4) is where they are kept, encrypted.
"""

import re
from dataclasses import dataclass

_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]

MARKERS = {
    "aadhaar": "[Aadhaar hidden]",
    "pan": "[PAN hidden]",
    "card": "[card number hidden]",
    "otp": "[OTP hidden]",
    "password": "[password hidden]",
}

_NOTICES = {
    "en": "For your safety I've hidden the Aadhaar, PAN, card number, OTP or password you "
    "shared. Please don't share these here.",
    "hi": "आपकी सुरक्षा के लिए मैंने आपका आधार, PAN, कार्ड नंबर, OTP या पासवर्ड छिपा दिया है। "
    "कृपया इन्हें यहाँ साझा न करें।",
    "hi-Latn": "Aapki safety ke liye maine Aadhaar, PAN, card number, OTP ya password chhupa "
    "diya hai. Kripya inhe yahan share na karein.",
}

_AADHAAR = re.compile(r"(?<!\d)([2-9]\d{3})[ -]?(\d{4})[ -]?(\d{4})(?!\d)")
_PAN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{5}\d{4}[A-Za-z])(?![A-Za-z0-9])")
_CARD = re.compile(r"(?<!\d)(\d(?:[ -]?\d){12,18})(?!\d)")
_OTP_BEFORE = re.compile(
    r"(?:\botp\b|one[- ]time password|verification code|ओटीपी|ओ टी पी)\D{0,20}?(\d{4,8})(?!\d)",
    re.IGNORECASE,
)
_OTP_AFTER = re.compile(
    r"(?<!\d)(\d{4,8})\s*(?:is|hai|है)?\s*(?:my|mera|मेरा)?\s*(?:\botp\b|ओटीपी)",
    re.IGNORECASE,
)
_PASSWORD = re.compile(
    r"(?:\bpassword\b|\bpasscode\b|\bpwd\b|\bpin\b|पासवर्ड)\s*(?:is|:|=|hai|है)?\s*(\S{4,64})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PiiHit:
    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class MaskResult:
    text: str
    kinds: list[str]


def verhoeff_valid(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_digit(number: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


def luhn_valid(number: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(number)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def find_pii(text: str) -> list[PiiHit]:
    """Every identifier, left to right, overlaps resolved in favour of the earlier kind."""
    hits: list[PiiHit] = []

    def free(start: int, end: int) -> bool:
        return all(end <= h.start or start >= h.end for h in hits)

    for m in _AADHAAR.finditer(text):
        if verhoeff_valid("".join(m.groups())) and free(m.start(), m.end()):
            hits.append(PiiHit("aadhaar", m.start(), m.end()))
    for m in _CARD.finditer(text):
        digits = _digits(m.group(1))
        if 13 <= len(digits) <= 19 and luhn_valid(digits) and free(m.start(), m.end()):
            hits.append(PiiHit("card", m.start(), m.end()))
    for m in _PAN.finditer(text):
        if m.group(1).upper()[3] in "ABCFGHLJPTK" and free(m.start(1), m.end(1)):
            hits.append(PiiHit("pan", m.start(1), m.end(1)))
    for pattern in (_OTP_BEFORE, _OTP_AFTER):
        for m in pattern.finditer(text):
            if free(m.start(1), m.end(1)):
                hits.append(PiiHit("otp", m.start(1), m.end(1)))
    for m in _PASSWORD.finditer(text):
        if free(m.start(1), m.end(1)):
            hits.append(PiiHit("password", m.start(1), m.end(1)))
    return sorted(hits, key=lambda h: h.start)


def mask_pii(text: str) -> MaskResult:
    hits = find_pii(text)
    if not hits:
        return MaskResult(text=text, kinds=[])
    out: list[str] = []
    cursor = 0
    kinds: list[str] = []
    for h in hits:
        out.append(text[cursor : h.start])
        out.append(MARKERS[h.kind])
        cursor = h.end
        if h.kind not in kinds:
            kinds.append(h.kind)
    out.append(text[cursor:])
    return MaskResult(text="".join(out), kinds=kinds)


def pii_notice(lang: str) -> str:
    return _NOTICES.get(lang, _NOTICES["en"])


# TV2 reference: contact details in text that goes to the business without the visitor's consent
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_MOBILE = re.compile(r"(?<!\d)(?:(?:\+|00)?91[\s-]?|0)?[6-9](?:[\s-]?\d){9}(?!\d)")
_LANDLINE = re.compile(r"(?<!\d)0(?:[\s-]?\d){9,10}(?!\d)")
_INTERNATIONAL = re.compile(r"(?<!\d)\+\d{1,3}(?:[\s-]?\d){8,11}(?!\d)")
_IDENTIFIER_BEFORE = re.compile(
    r"(?i)(?:\b(?:order|invoice|inv|pnr|booking|ticket|ref|id|no)|#)\.?\s*[:#]?\s*$"
)

# "mob no", "phone no.", "whatsapp id": the word names a phone number, not an order
_PHONE_WORD_BEFORE = re.compile(
    r"(?i)\b(?:mob|mobile|phone|ph|tel|telephone|contact|whatsapp|wa|cell|call|number)\b"
    r"\.?\s*(?:no|id|nos|num|number)?\.?\s*[:#]?\s*$"
)


def mask_contacts(text: str) -> str:
    """Phone numbers and e-mail addresses replaced by `[phone hidden]` / `[e-mail hidden]`.

    Only for text the business receives about someone who did not consent to share contact
    details (the visitor's own chat keeps them: they may be typed on purpose). Prices, times,
    dates and pin codes are left alone: a phone number has 10 to 13 digits behind a mobile or
    STD shape."""
    text = _EMAIL.sub("[e-mail hidden]", text)

    def hide(m: re.Match[str]) -> str:
        # an order, invoice, PNR or booking number is what the owner needs, not a phone number
        before = m.string[max(0, m.start() - 24) : m.start()]
        if _PHONE_WORD_BEFORE.search(before):
            return "[phone hidden]"
        return m.group(0) if _IDENTIFIER_BEFORE.search(before) else "[phone hidden]"

    for pattern in (_MOBILE, _INTERNATIONAL, _LANDLINE):
        text = pattern.sub(hide, text)
    return text


# /TV2 reference
