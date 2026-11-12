"""The text a WhatsApp chat opens with, so the person does not ask again (TV2, pure, no model).

`Ref SD-4F7K — <the visitor's last questions>`: the visitor's own words (already masked when
they were stored), visible and editable in WhatsApp before sending. Without a reference it is
the old `Hi! <question>` text.
"""

from typing import Final

from callobuzz_rag.conversation.handoff.channels import prefill_text

PREFILL_MAX: Final = 300
MAX_QUESTIONS: Final = 3
_MIN_OLDER: Final = 20
_LEAD_IN: Final[dict[str, str]] = {
    "en": "Ref {ref}",
    "hi": "संदर्भ {ref}",
    "hi-Latn": "Ref {ref}",
}


def _clean(questions: list[str]) -> list[str]:
    out: list[str] = []
    for q in questions:
        text = " ".join(q.split())
        if text and (not out or out[-1] != text):
            out.append(text)
    return out[-MAX_QUESTIONS:]


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[:limit]
    return (head[: head.rfind(" ")] if " " in head else head).rstrip()


def whatsapp_prefill(ref: str | None, questions: list[str], lang: str) -> str:
    """`Ref SD-4F7K — q1 · q2 · q3`, at most 300 characters. The budget is filled newest first, each
    cut on a word boundary with an ellipsis, so the oldest question is the one dropped."""
    cleaned = _clean(questions)
    if not ref:
        return prefill_text(cleaned[-1], None) if cleaned else "Hi!"
    lead = (_LEAD_IN.get(lang) or _LEAD_IN["en"]).replace("{ref}", ref)
    if not cleaned:
        return lead
    prefix = f"{lead} — "
    budget = PREFILL_MAX - len(prefix)
    kept: list[str] = []
    for q in reversed(cleaned):  # newest first: the latest question matters most
        sep = len(" · ") if kept else 0
        room = budget - sep
        if room < (_MIN_OLDER if kept else 2):  # no stub of an older question
            break
        piece = q if len(q) <= room else _cut(q, room - 1) + "…"
        if piece in ("", "…"):
            break
        kept.append(piece)
        budget -= sep + len(piece)
    if not kept:
        return lead
    return prefix + " · ".join(reversed(kept))
