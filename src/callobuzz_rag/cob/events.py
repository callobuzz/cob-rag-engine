"""COB's SSE frames and links: the `meta`, `sources`, `ui` and `done` events, and the deep link.

The director's own events are translated by `conversation/envelope.py` unchanged; COB re-shapes
three of them (`meta`, `sources`, `done`) and adds `ui` blocks. A source is a help page, never a
chunk id or a score. A link only ever points to a screen the caller's role may open.
"""

import re
import uuid
from collections.abc import Sequence
from typing import Any, Final

from callobuzz_rag.api.schemas.cob import (
    CobActionCardBlock,  # COB5
    CobContext,
    CobLinkBlock,
    CobSearchResultsBlock,
    CobSource,
)
from callobuzz_rag.api.streaming import sse_event
from callobuzz_rag.cob.config import cob_text
from callobuzz_rag.cob.kb import parse_external_id
from callobuzz_rag.cob.platform import PlatformHit, platform_hit
from callobuzz_rag.cob.types import CobRole, DocPage, DocsIndex, allowed_audiences

SNIPPET_CHARS: Final = 200
_WS = re.compile(r"\s+")


def _segments(path: str) -> list[str]:
    return [s for s in path.split("?", 1)[0].split("#", 1)[0].split("/") if s]


def _context_params(index: DocsIndex, context: CobContext | None) -> dict[str, str]:
    """The route parameters the person's current screen carries (`/kbs/<id>` gives `kbId`)."""
    values: dict[str, str] = {}
    if context is None:
        return values
    page = index.page_for_route(context.route)
    if page is not None and page.route and "$" in page.route:
        path = _segments(context.route)
        for i, seg in enumerate(_segments(page.route)):
            if seg.startswith("$") and len(seg) > 1 and i < len(path):
                values[seg[1:]] = path[i]
    if context.kb_id is not None:
        values.setdefault("kbId", str(context.kb_id))
    return values


def fill_route(template: str, index: DocsIndex, context: CobContext | None) -> str:
    """`template` with its `$params` filled from the screen the person is on. A parameter the
    context cannot supply cuts the path there: `/crawler/$jobId` becomes the list `/crawler`."""
    values = _context_params(index, context)
    out: list[str] = []
    for seg in template.split("/"):
        if seg.startswith("$"):
            name = seg[1:]
            if name and name in values:
                out.append(values[name])
                continue
            break
        out.append(seg)
    return "/".join(out) or "/"


def _visible(page: DocPage, role: CobRole) -> bool:
    return page.audience.value in allowed_audiences(role)


def deep_link(
    index: DocsIndex,
    *,
    cited: Sequence[str],
    role: CobRole,
    context: CobContext | None,
    language: str,
) -> CobLinkBlock | None:
    """The button under an answer: the first cited help page that is a screen the role may open;
    failing that, the first cited page as a Docs link. None when nothing cited is visible."""
    first_docs: DocPage | None = None
    for slug in cited:
        page = index.pages.get(slug)
        if page is None or not _visible(page, role):
            continue
        if page.route:
            return CobLinkBlock(
                label=cob_text("open_link", language, title=page.title),
                route=fill_route(page.route, index, context),
            )
        if first_docs is None:
            first_docs = page
    if first_docs is not None:
        return CobLinkBlock(
            label=cob_text("open_docs", language, title=first_docs.title),
            route=f"/docs/{first_docs.slug}",
            docs_slug=first_docs.slug,
        )
    return None


def snippet_of(text: str, title: str | None = None) -> str:
    """A short plain excerpt: the page title the record starts with is dropped."""
    body = text.strip()
    if title and body.startswith(title):
        body = body[len(title) :].lstrip()
    body = _WS.sub(" ", body)
    return body[:SNIPPET_CHARS] + ("..." if len(body) > SNIPPET_CHARS else "")


def cob_source(
    index: DocsIndex,
    *,
    position: int,
    slug: str,
    anchor: str,
    text: str,
    role: CobRole,
    context: CobContext | None,
) -> CobSource | None:
    """A help page COB used, or None when this role may not see it (defence in depth: the
    retrieval filter already excludes such records)."""
    page = index.pages.get(slug)
    if page is None or not _visible(page, role):
        return None
    section = next((s for s in page.sections if s.anchor == anchor), None)
    if section is not None and section.audience.value not in allowed_audiences(role):
        return None
    title = page.title if section is None or not anchor else f"{page.title} — {section.title}"
    return CobSource(
        index=position,
        slug=slug,
        anchor=anchor,
        title=title,
        snippet=snippet_of(text, page.title),
        route=fill_route(page.route, index, context) if page.route else None,
        public_url=page.public_url,
    )


def platform_source(hit: PlatformHit, position: int, raw: dict[str, Any]) -> CobSource:
    """A platform doc the operator added, as a source: no route or public page of its own."""
    return CobSource(
        index=position,
        slug=hit.slug,
        anchor="",
        title=hit.title,
        snippet=snippet_of(str(raw.get("text") or ""), hit.title),
    )


def sources_from_director(
    index: DocsIndex,
    sources: Sequence[dict[str, Any]],
    *,
    role: CobRole,
    context: CobContext | None,
) -> list[CobSource]:
    """The director's cited chunks as help pages: ids and scores dropped, one per record."""
    out: list[CobSource] = []
    seen: set[tuple[str, str]] = set()
    for raw in sources:
        parsed = parse_external_id(raw.get("external_id") or "")
        if parsed is None:
            hit = platform_hit(
                external_id=raw.get("external_id"),
                document_id=raw.get("document_id"),
                file_name=raw.get("file_name"),
                audience=raw.get("audience"),
                allowed=allowed_audiences(role),
            )
            if hit is not None and (hit.slug, "") not in seen:
                seen.add((hit.slug, ""))
                out.append(platform_source(hit, int(raw.get("index", len(out) + 1)), raw))
            continue
        if parsed in seen:
            continue
        mapped = cob_source(
            index,
            position=int(raw.get("index", len(out) + 1)),
            slug=parsed[0],
            anchor=parsed[1],
            text=str(raw.get("text") or ""),
            role=role,
            context=context,
        )
        if mapped is not None:
            seen.add(parsed)
            out.append(mapped)
    return out


def cob_meta(
    *,
    conversation_id: uuid.UUID | None,
    query_id: uuid.UUID | str,
    intent: str,
    mode: str,
    language: str,
    role: CobRole,
) -> str:
    return sse_event(
        "meta",
        {
            "conversation_id": str(conversation_id) if conversation_id else None,
            "query_id": str(query_id),
            "intent": intent,
            "mode": mode,
            "language": language,
            "role": role.value,
        },
    )


def cob_sources_event(sources: Sequence[CobSource]) -> str:
    return sse_event("sources", {"sources": [s.model_dump() for s in sources]})


def cob_ui_event(block: CobLinkBlock | CobSearchResultsBlock | CobActionCardBlock) -> str:  # COB5
    return sse_event("ui", block.model_dump(mode="json"))


def cob_done(
    *,
    message_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
    intent: str,
    mode: str,
    counted: bool,
    label: str | None,
    route: str | None,
    language: str,
    claims_removed: int,
    latency_ms: int,
) -> str:
    return sse_event(
        "done",
        {
            "message_id": str(message_id) if message_id else None,
            "conversation_id": str(conversation_id) if conversation_id else None,
            "intent": intent,
            "mode": mode,
            "counted": counted,
            "label": label,
            "route": route,
            "language": language,
            "claims_removed": claims_removed,
            "latency_ms": latency_ms,
        },
    )


def cob_error(detail: str, code: str) -> str:
    return sse_event("error", {"detail": detail, "code": code})
