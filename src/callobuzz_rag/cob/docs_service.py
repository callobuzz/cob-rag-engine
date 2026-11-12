"""The in-app Docs section: the tree, one page and search, for one caller's audiences.

Pages are rendered straight from the files in the image through `load_docs_index()` (cached by
manifest hash), so the Docs work before the first knowledge-base sync and can never disagree
with what COB reads. A page or section the caller may not read is simply absent: an unknown
page and a hidden page give the same 404, so nothing reveals what exists. Search runs over the
synced system knowledge base, retrieval only: no model call, never metered.
"""

import logging
import re
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.docs import (
    DocPageOut,
    DocsNeighbor,
    DocsSearchItem,
    DocsSearchOut,
    DocsTocItem,
    DocsTopic,
    DocsTree,
    DocsTreePage,
)
from callobuzz_rag.cob.kb import parse_external_id
from callobuzz_rag.cob.manifest import ManifestTopic, docs_root, load_manifest
from callobuzz_rag.cob.types import DocPage, DocsIndex

logger = logging.getLogger(__name__)

SNIPPET_CHARS: Final = 240
POOL: Final = 30
_MARKDOWN_NOISE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)|[`*_>#]")


@lru_cache(maxsize=4)
def _topics(root_str: str, digest: str) -> tuple[ManifestTopic, ...]:
    return tuple(load_manifest(Path(root_str)).topics)


def topics_for(index: DocsIndex, root: Path | None = None) -> tuple[ManifestTopic, ...]:
    """The manifest's topics, in order (cached per manifest hash)."""
    return _topics(str((root or docs_root()).resolve()), index.manifest_hash)


def _page_visible(page: DocPage, audiences: Sequence[str]) -> bool:
    return page.audience.value in audiences


def _topic_visible(topic: ManifestTopic, audiences: Sequence[str]) -> bool:
    return topic.audience is None or topic.audience.value in audiences


def _visible_in_topic(
    index: DocsIndex, topic: ManifestTopic, audiences: Sequence[str]
) -> list[DocPage]:
    if not _topic_visible(topic, audiences):
        return []
    return [p for p in index.pages.values() if p.topic == topic.id and _page_visible(p, audiences)]


def tree_for(
    index: DocsIndex, audiences: Sequence[str], topics: Sequence[ManifestTopic] | None = None
) -> DocsTree:
    """Topics with the pages `audiences` may read; topics left empty disappear."""
    out: list[DocsTopic] = []
    for topic in topics if topics is not None else topics_for(index):
        pages = _visible_in_topic(index, topic, audiences)
        if not pages:
            continue
        out.append(
            DocsTopic(
                id=topic.id,
                title=topic.title,
                pages=[
                    DocsTreePage(slug=p.slug, title=p.title, route=p.route, summary=p.summary)
                    for p in pages
                ],
            )
        )
    return DocsTree(version=index.version, topics=out)


def _not_found() -> APIError:
    return APIError(404, "That help page does not exist.", "docs_page_not_found")


def page_for(
    index: DocsIndex,
    slug: str,
    audiences: Sequence[str],
    topics: Sequence[ManifestTopic] | None = None,
) -> DocPageOut:
    """One page as Markdown with the sections `audiences` may read; 404 for a hidden or
    unknown page."""
    page = index.pages.get(slug)
    if page is None or not _page_visible(page, audiences):
        raise _not_found()
    topic = next(
        (t for t in (topics if topics is not None else topics_for(index)) if t.id == page.topic),
        None,
    )
    if topic is not None and not _topic_visible(topic, audiences):
        raise _not_found()
    allowed = set(audiences)
    parts: list[str] = []
    toc: list[DocsTocItem] = []
    for section in page.sections:
        if section.audience.value not in allowed:
            continue
        if section.anchor:
            parts.append(f"## {section.title}\n\n{section.text}")
            toc.append(DocsTocItem(anchor=section.anchor, title=section.title, level=2))
        else:
            parts.append(section.text)
    siblings = (
        _visible_in_topic(index, topic, audiences)
        if topic is not None
        else [p for p in index.visible(audiences) if p.topic == page.topic]
    )
    slugs = [p.slug for p in siblings]
    at = slugs.index(slug) if slug in slugs else -1
    prev_page = siblings[at - 1] if at > 0 else None
    next_page = siblings[at + 1] if 0 <= at < len(siblings) - 1 else None
    return DocPageOut(
        slug=page.slug,
        title=page.title,
        topic=page.topic,
        route=page.route,
        summary=page.summary,
        markdown="\n\n".join(parts),
        toc=toc,
        version=index.version,
        public_url=page.public_url,
        prev=DocsNeighbor(slug=prev_page.slug, title=prev_page.title) if prev_page else None,
        next=DocsNeighbor(slug=next_page.slug, title=next_page.title) if next_page else None,
    )


def _snippet(text: str) -> str:
    plain = _MARKDOWN_NOISE.sub(lambda m: m.group(1) or "", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    if len(plain) <= SNIPPET_CHARS:
        return plain
    return plain[:SNIPPET_CHARS].rsplit(" ", 1)[0].rstrip(".,;:") + "..."


def title_matches(index: DocsIndex, q: str, audiences: Sequence[str]) -> list[DocPage]:
    """Visible pages whose title starts with, or contains, the query (prefix matches first)."""
    needle = q.strip().lower()
    if not needle:
        return []
    visible = [p for p in index.visible(audiences) if _topic_ok(index, p, audiences)]
    starts = [p for p in visible if p.title.lower().startswith(needle)]
    contains = [p for p in visible if needle in p.title.lower() and p not in starts]
    return starts + contains


def _topic_ok(index: DocsIndex, page: DocPage, audiences: Sequence[str]) -> bool:
    topic = next((t for t in topics_for(index) if t.id == page.topic), None)
    return topic is None or _topic_visible(topic, audiences)


async def search(
    request: Any,
    session: Any,
    q: str,
    audiences: Sequence[str],
    *,
    limit: int = 10,
    index: DocsIndex,
) -> DocsSearchOut:
    """Pages matching `q`: title matches first, then the knowledge base's best sections grouped
    by page. Retrieval only (no model call, nothing metered). Raises 503
    `docs_search_unavailable` when the knowledge base cannot be searched."""
    from callobuzz_rag.cob.orchestrator import build_retriever
    from callobuzz_rag.cob.system import get_system_corpus

    items: list[DocsSearchItem] = []
    seen: set[str] = set()
    for matched in title_matches(index, q, audiences)[:limit]:
        seen.add(matched.slug)
        items.append(
            DocsSearchItem(
                slug=matched.slug,
                anchor="",
                title=matched.title,
                snippet=matched.summary[:SNIPPET_CHARS],
                route=matched.route,
                score=1.0,
            )
        )
    try:
        corpus = await get_system_corpus(session)
        if corpus is None:
            raise RuntimeError("the COB knowledge base does not exist yet")
        retriever = build_retriever(request, session, corpus)
        result = await retriever.run_retrieval_pipeline(
            q,
            corpus.id,
            top_k=POOL,
            final_k=POOL,
            enable_hyde=False,
            enable_classification=False,
            enable_mmr=False,
            enable_reranking=False,
            metadata_filters={"audience": list(audiences)},
        )
        chunks = list(result.assembled.cited_chunks)
    except Exception as exc:
        logger.warning("Docs search failed", exc_info=True)
        raise APIError(
            503,
            "Search is not available right now. Browse the pages instead.",
            "docs_search_unavailable",
        ) from exc
    allowed = set(audiences)
    for chunk in chunks:
        if len(items) >= limit:
            break
        parsed = parse_external_id(chunk.external_id or "")
        if parsed is None:
            from callobuzz_rag.cob.platform import platform_hit

            plat = platform_hit(
                external_id=chunk.external_id,
                document_id=chunk.document_id,
                file_name=chunk.file_name,
                audience=getattr(chunk, "audience", None),
                allowed=list(allowed),
            )
            if plat is not None and plat.slug not in seen:
                seen.add(plat.slug)
                items.append(
                    DocsSearchItem(
                        slug=plat.slug,
                        anchor="",
                        title=plat.title,
                        snippet=_snippet(chunk.text),
                        route=None,
                        score=round(float(chunk.score), 4),
                    )
                )
            continue
        if parsed[0] in seen:
            continue
        slug, anchor = parsed
        hit = index.pages.get(slug)
        if hit is None or not _page_visible(hit, audiences) or not _topic_ok(index, hit, audiences):
            continue
        section = next((s for s in hit.sections if s.anchor == anchor), None)
        if section is None or section.audience.value not in allowed:
            continue
        seen.add(slug)
        title = hit.title if not anchor else f"{hit.title} — {section.title}"
        items.append(
            DocsSearchItem(
                slug=slug,
                anchor=anchor,
                title=title,
                snippet=_snippet(section.text),
                route=hit.route,
                score=round(float(chunk.score), 4),
            )
        )
    return DocsSearchOut(items=items)


# COB9: pages the operator added ("From your platform")
PLATFORM_TOPIC_ID: Final = "platform"
PLATFORM_TOPIC_TITLE: Final = "From your platform"
PLATFORM_SLUG_PREFIX: Final = "platform/"


async def _platform_documents(session: Any, audiences: Sequence[str]) -> list[Any]:
    """The operator's documents the caller may read, newest first."""
    from sqlalchemy import select

    from callobuzz_rag.cob.platform import PLATFORM, SOURCE_KEY
    from callobuzz_rag.cob.system import get_system_corpus
    from callobuzz_rag.db.models.document import Document

    corpus = await get_system_corpus(session)
    if corpus is None:
        return []
    rows = (
        (
            await session.execute(
                select(Document)
                .where(Document.corpus_id == corpus.id)
                .order_by(Document.ingested_at.desc())
            )
        )
        .scalars()
        .all()
    )
    allowed = set(audiences)
    return [
        d
        for d in rows
        if (d.doc_metadata or {}).get(SOURCE_KEY) == PLATFORM
        and (d.doc_metadata or {}).get("audience", "customer") in allowed
    ]


def _platform_title(doc: Any) -> str:
    return str(doc.file_name or doc.external_id or "Untitled")


async def platform_topic(session: Any, audiences: Sequence[str]) -> DocsTopic | None:
    docs = await _platform_documents(session, audiences)
    if not docs:
        return None
    return DocsTopic(
        id=PLATFORM_TOPIC_ID,
        title=PLATFORM_TOPIC_TITLE,
        pages=[
            DocsTreePage(slug=f"{PLATFORM_SLUG_PREFIX}{d.id}", title=_platform_title(d))
            for d in docs
        ],
    )


async def platform_page(
    session: Any, slug: str, audiences: Sequence[str], version: str
) -> DocPageOut:
    """One platform document as a page: its stored text, in reading order. A document the
    caller may not read is the same 404 as an unknown one."""
    from sqlalchemy import select

    from callobuzz_rag.db.models.chunk import Chunk
    from callobuzz_rag.db.models.document_version import DocumentVersion

    docs = await _platform_documents(session, audiences)
    ids = [f"{PLATFORM_SLUG_PREFIX}{d.id}" for d in docs]
    if slug not in ids:
        raise _not_found()
    at = ids.index(slug)
    doc = docs[at]
    texts = (
        (
            await session.execute(
                select(Chunk.text)
                .join(DocumentVersion, Chunk.document_version_id == DocumentVersion.id)
                .where(
                    DocumentVersion.document_id == doc.id,
                    DocumentVersion.status == "active",
                    Chunk.parent_chunk_id.is_(None),
                )
                .order_by(Chunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )
    before = docs[at - 1] if at > 0 else None
    after = docs[at + 1] if at < len(docs) - 1 else None
    return DocPageOut(
        slug=slug,
        title=_platform_title(doc),
        topic=PLATFORM_TOPIC_ID,
        markdown="\n\n".join(texts),
        toc=[],
        version=version,
        prev=DocsNeighbor(slug=ids[at - 1], title=_platform_title(before)) if before else None,
        next=DocsNeighbor(slug=ids[at + 1], title=_platform_title(after)) if after else None,
    )
