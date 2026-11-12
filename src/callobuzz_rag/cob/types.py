"""COB's shared types: who is asking (roles and audiences) and what the docs look like.

A document's `audience` is the *least* privileged role that may read it. A role may read every
audience listed for it in `ALLOWED_AUDIENCES`; retrieval always filters on that list, set on the
server from the caller's role, so admin-only text is never retrieved for a customer.
"""

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final


class Audience(StrEnum):
    ADMIN = "admin"
    CUSTOMER = "customer"
    PUBLIC = "public"


class CobRole(StrEnum):
    # A super-admin session in the operator view.
    ADMIN = "admin"
    # A super admin inside the operator workspace (reads admin docs; tools act on the workspace).
    OPERATOR_OWNER = "operator_owner"
    OWNER = "owner"
    MEMBER = "member"
    # No session (the public COB on the sign-in pages).
    PUBLIC = "public"


ALLOWED_AUDIENCES: Final[dict[CobRole, tuple[Audience, ...]]] = {
    CobRole.ADMIN: (Audience.ADMIN, Audience.CUSTOMER, Audience.PUBLIC),
    CobRole.OPERATOR_OWNER: (Audience.ADMIN, Audience.CUSTOMER, Audience.PUBLIC),
    CobRole.OWNER: (Audience.CUSTOMER, Audience.PUBLIC),
    CobRole.MEMBER: (Audience.CUSTOMER, Audience.PUBLIC),
    CobRole.PUBLIC: (Audience.PUBLIC,),
}


def allowed_audiences(role: CobRole) -> list[str]:
    """The audience values `role` may read, as the retrieval filter wants them."""
    return [a.value for a in ALLOWED_AUDIENCES[role]]


@dataclass(frozen=True)
class CobCaller:
    role: CobRole
    # None only for PUBLIC.
    user_id: uuid.UUID | None
    # The workspace (None for ADMIN and PUBLIC).
    tenant_id: uuid.UUID | None
    session_id: uuid.UUID | None
    # The caller's own "Bearer ..." header, for internal tool calls. Never logged.
    authorization: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class DocSection:
    """One retrieval record."""

    anchor: str  # slugified heading ("embed-the-widget"); "" = the page intro
    title: str
    text: str  # Markdown
    audience: Audience


@dataclass(frozen=True)
class DocPage:
    slug: str  # "screens/widget", "guide/flow", "api/assistant", "settings/llm"
    title: str
    topic: str
    audience: Audience  # the page's own
    route: str | None  # dashboard route (screen pages only)
    summary: str
    starters: tuple[str, ...]
    admin_starters: tuple[str, ...]
    source_id: str
    public_url: str | None
    sections: tuple[DocSection, ...]


_PARAM = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")


def _template_regex(route: str) -> re.Pattern[str]:
    segments = route.split("/")
    splat = len(segments) > 1 and segments[-1] == "$"  # a splat takes the rest, even nothing
    parts = []
    for seg in segments[:-1] if splat else segments:
        parts.append("[^/]+" if _PARAM.fullmatch(seg) else re.escape(seg))
    return re.compile("^" + "/".join(parts) + ("(?:/.*)?" if splat else "") + "$")


def _clean_route(route: str) -> str:
    path = route.split("?", 1)[0].split("#", 1)[0]
    if len(path) > 1:
        path = path.rstrip("/")
    return path or "/"


@dataclass(frozen=True)
class DocsIndex:
    version: str  # f"{__version__}+{manifest_hash[:12]}"
    manifest_hash: str  # sha256 over the manifest and every source file
    pages: dict[str, DocPage]

    def page_for_route(self, route: str) -> DocPage | None:
        """The screen page for a dashboard path: exact, then by route template, then the
        nearest parent path (`/kbs/<id>/x` falls back to `/kbs/<id>`, then `/kbs`)."""
        by_route = {p.route: p for p in self.pages.values() if p.route}
        path = _clean_route(route)
        while True:
            if path in by_route:
                return by_route[path]
            best: DocPage | None = None
            best_literals = -1
            for template, page in by_route.items():
                if "$" not in template or not _template_regex(template).match(path):
                    continue
                literals = sum(1 for seg in template.split("/") if seg and "$" not in seg)
                if literals > best_literals:
                    best, best_literals = page, literals
            if best is not None:
                return best
            if path == "/":
                return None
            path = path.rsplit("/", 1)[0] or "/"

    def visible(self, audiences: Sequence[str]) -> list[DocPage]:
        allowed = set(audiences)
        return [p for p in self.pages.values() if p.audience.value in allowed]
