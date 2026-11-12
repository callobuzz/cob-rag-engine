"""What every action tool shares: a draft of the card, the outcome of running it, and the
helpers that turn a route's answer into a sentence.

An action tool never writes anything while it proposes: it reads through its declared GET routes
(so only what the person may see is a candidate), resolves the names in their words to ids and
returns a `ProposalDraft`. Only `execute`, called by `cob/proposals.py` after a confirm, calls the
tool's write route with the caller's own token, so the real route applies its own permission
checks, caps and audit exactly as it does for the dashboard.
"""

import re
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal
from urllib.parse import urlsplit

from callobuzz_rag.api.schemas.cob import CobLinkBlock
from callobuzz_rag.cob.tools.base import ActionTool, RouteCall, ToolContext, ToolResult
from callobuzz_rag.cob.tools.cards import esc, link_to, ts
from callobuzz_rag.cob.tools.http import InternalResponse, ToolFailure
from callobuzz_rag.cob.tools.resolve import (
    Ambiguous,
    NotFound,
    Resolved,
    clarify,
    list_kbs,
    resolve_kb,
)
from callobuzz_rag.core.schemas import StrictRequest

Risk = Literal["low", "medium", "high"]


@dataclass
class ProposalDraft:
    """The card for one proposed action. `exec_args` (ids and names, never secrets) is what is
    stored and re-validated when the person confirms."""

    title: str
    summary: str  # Markdown, built from a template, never by the model
    args: list[tuple[str, str]]  # label, value: shown on the card
    risk: Risk
    exec_args: dict[str, Any]
    confirm_name: str | None = None  # the exact name to type (destructive tools only)


@dataclass
class ActionOutcome:
    """What a confirmed action did. `message` is plain Markdown without any secret; `secret`
    (a rotated key) is returned to the person once and stored nowhere."""

    method: str
    path: str  # the route template, e.g. "/api/v1/corpora/{corpus_id}/widget-keys"
    status: int
    ok: bool
    message: str
    code: str | None = None
    link: CobLinkBlock | None = None
    secret: str | None = None
    result: dict[str, Any] = field(default_factory=dict)  # ids only


class ActionBase(ActionTool):
    """An action tool: what it asks (`args_model`, the person's own words), what it stores
    (`exec_model`, resolved ids), and how it is shown."""

    exec_model: ClassVar[type[StrictRequest]]
    risk: ClassVar[Risk]
    screen: ClassVar[str]

    async def current_name(self, ctx: ToolContext, exec_args: dict[str, Any]) -> str:
        """The resource's name right now, read through a GET route (destructive tools only)."""
        raise NotImplementedError

    @property
    def primary(self) -> RouteCall:
        """The write route (every action tool declares it first), for audit when it never ran."""
        return self.routes[0]

    # ---------------------------------------------------------------- helpers

    def link(self, ctx: ToolContext, route: str | None = None) -> CobLinkBlock:
        return link_to(self, ctx, route or self.screen)

    def done(
        self,
        ctx: ToolContext,
        response: InternalResponse,
        method: str,
        path: str,
        message: str,
        *,
        link: str | None = None,
        secret: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> ActionOutcome:
        """The outcome of a write: the route's own status and error code are passed through."""
        if response.ok:
            return ActionOutcome(
                method=method,
                path=path,
                status=response.status,
                ok=True,
                message=message,
                link=self.link(ctx, link),
                secret=secret,
                result=result or {},
            )
        body = response.body if isinstance(response.body, dict) else {}
        reason = body.get("detail")
        detail = (
            reason if isinstance(reason, str) and reason else ts("action_no_reason", ctx.language)
        )
        code = body.get("code")
        return ActionOutcome(
            method=method,
            path=path,
            status=response.status,
            ok=False,
            message=detail,
            code=code if isinstance(code, str) else None,
            link=self.link(ctx, link),
        )


# ------------------------------------------------------------------ resolving words


async def one_kb(ctx: ToolContext, name: str | None) -> dict[str, Any] | ToolResult:
    """The knowledge base the person means, or the short question / refusal to send instead."""
    try:
        kbs = await list_kbs(ctx)
    except ToolFailure:
        return ToolResult(markdown=ts("unavailable", ctx.language), ok=False)
    if not kbs:
        return ToolResult(markdown=ts("kbs_none", ctx.language), ok=False)
    found = await resolve_kb(ctx, name, kbs)
    if isinstance(found, Resolved):
        return found.kb
    if isinstance(found, NotFound):
        return ToolResult(
            markdown=ts("not_found", ctx.language, name=f"**{esc(found.name, 60)}**"), ok=False
        )
    assert isinstance(found, Ambiguous)
    return clarify(ctx, found.candidates)


def ask(ctx: ToolContext, key: str, **values: Any) -> ToolResult:
    """A short question: the proposal waits until the person answers."""
    return ToolResult(markdown=ts(key, ctx.language, **values), data={"ask": key})


_URL = re.compile(
    r"(https?://[^\s,;<>\"')]+|(?<![\w@.])(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s,;<>\"')]*)?)", re.I
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def origin_of(raw: str) -> str | None:
    """`shreelabs.in/about` as `https://shreelabs.in`; None when it is not a website."""
    value = raw.strip().rstrip(".,;")
    if not value or value == "*":
        return None
    if "://" not in value:
        value = "https://" + value
    try:
        parts = urlsplit(value)
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        return None
    host = parts.hostname
    if not host or ("." not in host and host != "localhost"):
        return None
    return f"{parts.scheme}://{host}{port}".lower()


def site_in(text: str) -> str | None:
    """The first website named in `text`, as an origin. An e-mail address is not a website."""
    match = _URL.search(_EMAIL.sub(" ", text or ""))
    return origin_of(match.group(0)) if match else None


def email_in(text: str) -> str | None:
    match = _EMAIL.search(text or "")
    return match.group(0).lower() if match else None


def _key(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def pick_by_name(
    items: list[dict[str, Any]], wanted: str | None, question: str, field_name: str = "name"
) -> list[dict[str, Any]]:
    """Items called `wanted` (exact, case-blind, else containing it); with no name, the ones
    whose name appears in the person's own words (the longest when several do)."""
    if wanted:
        w = _key(wanted)
        exact = [i for i in items if _key(i.get(field_name)) == w]
        return exact or [i for i in items if w and w in _key(i.get(field_name))]
    q = _key(question)
    found = [i for i in items if len(_key(i.get(field_name))) >= 3 and _key(i.get(field_name)) in q]
    if len(found) > 1:
        longest = max(len(_key(i.get(field_name))) for i in found)
        found = [i for i in found if len(_key(i.get(field_name))) == longest]
    return found
