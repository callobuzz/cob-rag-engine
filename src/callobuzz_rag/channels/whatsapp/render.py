"""Markdown to WhatsApp (TV6): what the assistant wrote, shaped for a chat bubble.

Pure. WhatsApp has its own light markup (`*bold*`, `_italic_`, `~strike~`, a bullet is a plain
`•`), no links text (a link is shown as its address), a 4,096-character limit per text message,
and at most three quick-reply buttons of 20 characters. This module turns the director's
`DoneEvent` into the messages to send. It never builds a WhatsApp link (the customer is already
in WhatsApp) and never renders a lead form (WhatsApp collects contact details in the
conversation, with consent first).
"""

import re
from dataclasses import dataclass, field
from typing import Any

TEXT_LIMIT = 4096
MAX_TEXTS = 3
BUTTON_BODY_LIMIT = 1024
MAX_BUTTONS = 3
MAX_BUTTON_TITLE = 20
QUICK_REPLY_PREFIX = "qr:"
ELLIPSIS = "…"

_PERSON = {
    "en": "A person from {business} will reply here.",
    "hi": "{business} की टीम से कोई व्यक्ति यहीं आपको जवाब देगा।",
    "hi-Latn": "{business} ki team se koi yahin aapko jawab dega.",
}
_REFERENCE = {
    "en": "Your reference is {ref}.",
    "hi": "आपका संदर्भ {ref} है।",
    "hi-Latn": "Aapka reference {ref} hai.",
}
_LINE_LABELS: dict[str, dict[str, str]] = {
    "call": {"en": "Call", "hi": "कॉल", "hi-Latn": "Call"},
    "email": {"en": "E-mail", "hi": "ई-मेल", "hi-Latn": "Email"},
    "booking": {"en": "Book", "hi": "बुक करें", "hi-Latn": "Book karein"},
    "contact_page": {"en": "Contact page", "hi": "संपर्क पृष्ठ", "hi-Latn": "Contact page"},
    "link": {"en": "More", "hi": "और जानें", "hi-Latn": "Aur jaanein"},
}
_PICK = {"en": "Choose one:", "hi": "एक चुनें:", "hi-Latn": "Ek chunein:"}
_TRIMMED = ELLIPSIS


def pick(table: dict[str, str], lang: str) -> str:
    return table.get(lang) or table["en"]


# ------------------------------------------------------------------ Markdown -> WhatsApp

_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
_BOLD_ITALIC = re.compile(r"(\*\*\*|___)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_ITALIC_STAR = re.compile(r"(?<![\*\w])\*(?=[^\s\*])([^\*\n]+?)(?<=[^\s\*])\*(?![\*\w])")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_CITATION = re.compile(r"[ \t]*\[\d{1,3}\](?!\()")
_BULLET = re.compile(r"^([ \t]*)[-*+][ \t]+", re.MULTILINE)
_RULE = re.compile(r"^[ \t]*([-*_])([ \t]*\1){2,}[ \t]*$", re.MULTILINE)
_BLANKS = re.compile(r"\n{3,}")
_SPACES_BEFORE_PUNCT = re.compile(r"[ \t]+([.,;:!?])")
_MARK = "\x00"


_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table_rows(header: list[str], rows: list[list[str]]) -> list[str]:
    """A Markdown table as plain lines (WhatsApp has no tables): two columns read "a: b",
    more read "a - B: b, C: c"."""
    lines: list[str] = []
    for cells in rows:
        cells = [*cells, *[""] * (len(header) - len(cells))]
        first, rest = cells[0], cells[1:]
        if len(header) == 2:
            lines.append(f"• {first}: {rest[0]}" if rest[0] else f"• {first}")
            continue
        parts = [f"{header[i + 1]}: {v}" for i, v in enumerate(rest) if v and i + 1 < len(header)]
        lines.append(f"• {first} — " + ", ".join(parts) if parts else f"• {first}")
    return lines


def _flatten_tables(text: str) -> str:
    """Replace each Markdown table (a header line, a `---` separator line, rows) by bullets."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        is_table = (
            "|" in lines[i]
            and i + 1 < len(lines)
            and "|" in lines[i + 1]
            and _TABLE_SEPARATOR.match(lines[i + 1]) is not None
        )
        if not is_table:
            out.append(lines[i])
            i += 1
            continue
        header = _cells(lines[i])
        i += 2
        rows: list[list[str]] = []
        while i < len(lines) and "|" in lines[i] and lines[i].strip():
            rows.append(_cells(lines[i]))
            i += 1
        out.extend(_table_rows(header, rows))
    return "\n".join(out)


def to_whatsapp(markdown: str) -> str:
    """The text with Markdown replaced by WhatsApp's markup, citation markers removed."""
    text = _flatten_tables(markdown.replace("\r\n", "\n"))
    text = _CITATION.sub("", text)
    text = _LINK.sub(
        lambda m: m.group(2) if m.group(1) == m.group(2) else f"{m.group(1)}: {m.group(2)}", text
    )
    text = _HEADING.sub(lambda m: f"{_MARK}{m.group(1).strip()}{_MARK}", text)
    text = _BOLD_ITALIC.sub(lambda m: f"{_MARK}_{m.group(2).strip()}_{_MARK}", text)
    text = _BOLD.sub(lambda m: f"{_MARK}{m.group(2).strip()}{_MARK}", text)
    text = _ITALIC_STAR.sub(lambda m: f"_{m.group(1).strip()}_", text)
    text = text.replace(_MARK, "*")
    text = _RULE.sub("", text)
    text = _BULLET.sub(lambda m: f"{m.group(1)}• ", text)
    text = _SPACES_BEFORE_PUNCT.sub(r"\1", text)
    return _BLANKS.sub("\n\n", text).strip()


# ------------------------------------------------------------------ splitting


def _hard_split(piece: str, limit: int) -> list[str]:
    """A paragraph longer than the limit, cut at the last space (or line) before it."""
    out: list[str] = []
    rest = piece
    while len(rest) > limit:
        cut = max(
            rest.rfind("\n", 0, limit), rest.rfind(". ", 0, limit) + 1, rest.rfind(" ", 0, limit)
        )
        if cut <= 0:
            cut = limit
        out.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        out.append(rest)
    return out


def split_messages(
    text: str, *, limit: int = TEXT_LIMIT, max_messages: int = MAX_TEXTS
) -> list[str]:
    """`text` as at most `max_messages` messages of at most `limit` characters, split on
    paragraph boundaries; what does not fit is dropped and the last message ends with `…`."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for paragraph in re.split(r"\n{2,}", text):
        pieces = _hard_split(paragraph, limit) if len(paragraph) > limit else [paragraph]
        for piece in pieces:
            joined = f"{current}\n\n{piece}" if current else piece
            if len(joined) <= limit:
                current = joined
            else:
                chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    if len(chunks) > max_messages:
        chunks = chunks[:max_messages]
        last = chunks[-1]
        if len(last) + 1 > limit:
            last = last[: limit - 1].rstrip()
        chunks[-1] = last + _TRIMMED
    return chunks


# ------------------------------------------------------------------ the reply


@dataclass
class Rendered:
    """What to send, in order: texts, then (maybe) one message with buttons."""

    texts: list[str] = field(default_factory=list)
    button_body: str = ""
    buttons: list[tuple[str, str]] = field(default_factory=list)  # (id, title)

    @property
    def empty(self) -> bool:
        return not self.texts and not self.buttons


def _tel_display(url: str) -> str:
    number = url.removeprefix("tel:").strip()
    digits = re.sub(r"\D", "", number)
    if number.startswith("+91") and len(digits) == 12:
        return f"+91 {digits[2:7]} {digits[7:]}"
    return number


def _card_lines(card: Any, lang: str) -> list[str]:
    """The ways to reach the business that work outside WhatsApp: a number to call, an e-mail
    address, a booking or contact page. No WhatsApp link, and no call-back form."""
    lines: list[str] = []
    for channel in card.channels:
        kind, url = channel.kind, channel.url
        if kind in ("whatsapp", "callback") or not url:
            continue
        label = pick(_LINE_LABELS[kind], lang) if kind in _LINE_LABELS else channel.label
        if kind == "call":
            lines.append(f"{label}: {_tel_display(url)}")
        elif kind == "email":
            lines.append(f"{label}: {url.removeprefix('mailto:').split('?')[0]}")
        else:
            lines.append(f"{label}: {url}")
    return lines


def quick_reply_message(button_id: str) -> str | None:
    """The message a tapped quick-reply button stands for, or None for another button."""
    if button_id.startswith(QUICK_REPLY_PREFIX):
        return button_id[len(QUICK_REPLY_PREFIX) :]
    return None


def render_done(done: Any, *, business_name: str, lang: str | None = None) -> Rendered:
    """The messages for one answered turn (a `DoneEvent`)."""
    lang = lang or done.language or "en"
    parts: list[str] = []
    answer = to_whatsapp(done.text or "")
    if answer:
        parts.append(answer)
    card = done.ui if getattr(done.ui, "kind", None) == "handoff_card" else None
    if card is not None:
        # The card's own message is the contact sentence (and the opener when closed).
        if business_name:
            parts.append(pick(_PERSON, lang).replace("{business}", business_name))
        message = to_whatsapp(card.message or "")
        if message and message not in answer:
            parts.append(message)
        ref = getattr(card, "ref_code", None)
        if ref and ref not in "\n".join(parts):
            parts.append(pick(_REFERENCE, lang).replace("{ref}", ref))
        lines = _card_lines(card, lang)
        if lines:
            parts.append("\n".join(lines))
    cta = getattr(done, "cta", None)
    if cta is not None and cta.type in ("link", "booking", "call") and cta.url:
        shown = _tel_display(cta.url) if cta.url.startswith("tel:") else cta.url
        parts.append(f"{cta.label}: {shown}")
    # a lead form (`done.ui.kind == "lead_form"`) is never rendered: WhatsApp asks in the chat

    texts = split_messages("\n\n".join(parts))
    rendered = Rendered(texts=texts)
    replies = list(done.quick_replies or [])
    if not replies:
        return rendered
    fits = len(replies) <= MAX_BUTTONS and all(
        0 < len(r.label) <= MAX_BUTTON_TITLE for r in replies
    )
    if fits:
        rendered.buttons = [
            (QUICK_REPLY_PREFIX + r.message[: 256 - len(QUICK_REPLY_PREFIX)], r.label)
            for r in replies
        ]
        if texts and len(texts[-1]) <= BUTTON_BODY_LIMIT:
            rendered.button_body = rendered.texts.pop()  # the answer is the buttons' body
        else:
            rendered.button_body = pick(_PICK, lang)
        return rendered
    numbered = "\n".join(f"{i}. {r.label}" for i, r in enumerate(replies, start=1))
    rendered.texts = split_messages("\n\n".join([*texts, numbered]))
    return rendered
