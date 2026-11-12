"""Which knowledge base does the person mean? Tools take names, never ids.

The knowledge bases are listed through the tool's own `GET /api/v1/corpora` call (so only the
ones the person may see are ever candidates). A name comes from the router's arguments or, with
no model involved, from the person's own words: "my clinic KB" finds the knowledge base called
"Clinic". With no name the screen the person is on (`context.kb_id`) decides, then the only
knowledge base there is. Still unclear: a short question with the candidates as quick replies.
"""

from dataclasses import dataclass
from typing import Any, Final

from callobuzz_rag.cob.tools.base import ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, ts
from callobuzz_rag.cob.tools.http import fetch
from callobuzz_rag.conversation.schema import QuickReply

MAX_CANDIDATES: Final = 5
_MIN_NAME: Final = 3


@dataclass
class Resolved:
    kb: dict[str, Any]


@dataclass
class Ambiguous:
    candidates: list[dict[str, Any]]


@dataclass
class NotFound:
    name: str


def _key(text: Any) -> str:
    return " ".join(str(text or "").casefold().split())


async def list_kbs(ctx: ToolContext) -> list[dict[str, Any]]:
    body = await fetch(ctx.client, "/api/v1/corpora", {"limit": 100})
    items = body.get("items") if isinstance(body, dict) else None
    return [k for k in (items or []) if isinstance(k, dict)]


def match_kbs(name: str, kbs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Knowledge bases called `name`: an exact (case-blind) match wins; else those whose name
    contains it or is contained in it ("clinic kb" finds "Clinic")."""
    wanted = _key(name)
    if not wanted:
        return []
    exact = [k for k in kbs if _key(k.get("name")) == wanted]
    if exact:
        return exact
    return [
        k
        for k in kbs
        if wanted in _key(k.get("name"))
        or (len(_key(k.get("name"))) >= _MIN_NAME and _key(k.get("name")) in wanted)
    ]


def named_in(question: str, kbs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Knowledge bases whose name appears in the person's own words."""
    text = _key(question)
    found = [
        k for k in kbs if len(_key(k.get("name"))) >= _MIN_NAME and _key(k.get("name")) in text
    ]
    if len(found) > 1:  # "Clinic" and "Clinic Hindi" both appear: the longest name is meant
        longest = max(len(_key(k.get("name"))) for k in found)
        found = [k for k in found if len(_key(k.get("name"))) == longest]
    return found


def _on_page(ctx: ToolContext, kbs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if ctx.page is None or ctx.page.kb_id is None:
        return []
    return [k for k in kbs if str(k.get("id")) == str(ctx.page.kb_id)]


async def pick_kbs(
    ctx: ToolContext, name: str | None, kbs: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]] | Ambiguous | NotFound:
    """The knowledge bases a card should cover: the one named, else the one named in the
    question, else the one on screen, else all of them."""
    listed = kbs if kbs is not None else await list_kbs(ctx)
    if name:
        found = match_kbs(name, listed)
        if not found:
            return NotFound(name)
        return found if len(found) == 1 else Ambiguous(found)
    found = named_in(ctx.question, listed) or _on_page(ctx, listed)
    return found or listed


async def resolve_kb(
    ctx: ToolContext, name: str | None, kbs: list[dict[str, Any]] | None = None
) -> Resolved | Ambiguous | NotFound:
    """Exactly one knowledge base, or why not."""
    picked = await pick_kbs(ctx, name, kbs)
    if isinstance(picked, Ambiguous | NotFound):
        return picked
    if len(picked) == 1:
        return Resolved(picked[0])
    return Ambiguous(picked)


def clarify(ctx: ToolContext, candidates: list[dict[str, Any]]) -> ToolResult:
    """A short question naming the candidates, each a quick reply that asks again with it."""
    replies = []
    for kb in candidates[:MAX_CANDIDATES]:
        name = str(kb.get("name") or "")
        message = f"{name}: {ctx.question}".strip(": ") if ctx.question else name
        replies.append(QuickReply(label=name[:20] or "?", message=message[:200]))
    names = ", ".join(f"**{esc(k.get('name'), 40)}**" for k in candidates[:MAX_CANDIDATES])
    text = f"{ts('which_kb', ctx.language)} {names}"
    return ToolResult(markdown=text, quick_replies=replies, ok=True, data={"clarify": True})
