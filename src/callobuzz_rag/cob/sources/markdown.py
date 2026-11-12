"""Markdown files to `DocPage`s.

`split="page"`: the file is one page; each `## ` heading starts a section (a retrieval record).
`split="section"`: each `## ` heading is its own page, and each `### ` heading inside it a section.

A heading followed on the next line by `<!-- audience: admin -->` makes that section carry that
audience (for a `split="section"` page, the `## ` marker sets the whole page's audience).
"""

import re

from callobuzz_rag.cob.manifest import ManifestError, ManifestSource
from callobuzz_rag.cob.sources.common import (
    Anchors,
    override_for,
    parse_marker,
    slugify,
    summary_of,
)
from callobuzz_rag.cob.sources.frontmatter import FrontMatter, split_front_matter
from callobuzz_rag.cob.types import Audience, DocPage, DocSection

_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_COMMENT = re.compile(r"<!--(?!\s*audience:).*?-->", re.DOTALL)
_BLANKS = re.compile(r"\n{3,}")
# Text that counts as an intro worth a page: not just badges, images and a title.
_MEANINGFUL = 40


class _Part:
    """A run of lines under one heading."""

    def __init__(self, title: str, marker: Audience | None, lines: list[str]) -> None:
        self.title = title
        self.marker = marker
        self.lines = lines


def _heading(line: str, level: int) -> str | None:
    match = re.match(rf"^#{{{level}}}[ \t]+(.+?)[ \t]*#*[ \t]*$", line)
    return match.group(1).strip() if match else None


def _split(
    lines: list[str], level: int, where: str, *, inner_level: int | None = None
) -> tuple[list[str], list[_Part]]:
    """Lines before the first heading of `level`, and the parts after each heading of it
    (fenced code ignored).

    An audience marker is only taken on the line right after a heading of `level` (or, when
    `inner_level` is given, right after a heading of that level, which a later split takes).
    Any other marker line would be ignored and its text published to the page's audience, so it
    is an error instead."""
    intro: list[str] = []
    parts: list[_Part] = []
    in_fence = False
    current: _Part | None = None
    pending_marker_for: _Part | None = None
    after_inner = False
    for line in lines:
        if _FENCE.match(line):
            in_fence = not in_fence
        title = None if in_fence else _heading(line, level)
        was_after_inner, after_inner = after_inner, False
        if title is not None:
            current = _Part(title, None, [])
            parts.append(current)
            pending_marker_for = current
            continue
        if not in_fence and inner_level is not None and _heading(line, inner_level) is not None:
            after_inner = True
        if pending_marker_for is not None:
            marker = parse_marker(line, where)
            pending_marker_for_part, pending_marker_for = pending_marker_for, None
            if marker is not None:
                pending_marker_for_part.marker = marker
                continue
        elif not in_fence and not was_after_inner and parse_marker(line, where) is not None:
            raise ManifestError(
                f"{where}: an audience marker must be on the line right after a "
                f"'{'#' * (inner_level or level)} ' heading (found a stray one: {line.strip()})."
            )
        (current.lines if current is not None else intro).append(line)
    return intro, parts


def _clean(lines: list[str]) -> str:
    text = _COMMENT.sub("", "\n".join(lines))
    return _BLANKS.sub("\n\n", text).strip()


def _first_h1(lines: list[str]) -> tuple[str | None, list[str]]:
    """The first `# ` heading outside code, and the lines without it."""
    in_fence = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
        if not in_fence:
            title = _heading(line, 1)
            if title is not None:
                return title, lines[:i] + lines[i + 1 :]
    return None, lines


def _sections(
    intro_title: str,
    intro: list[str],
    parts: list[_Part],
    default: Audience,
    anchors: Anchors,
    *,
    override_source: ManifestSource | None,
    seen: set[str] | None,
) -> tuple[DocSection, ...]:
    out: list[DocSection] = []
    intro_text = _clean(intro)
    if intro_text:
        out.append(DocSection("", intro_title, intro_text, default))
    for part in parts:
        text = _clean(part.lines)
        if not text:
            continue
        anchor = anchors.take(slugify(part.title))
        audience = part.marker or default
        if override_source is not None:
            over, _topic = override_for(override_source, anchor, seen)
            audience = over or audience
        out.append(DocSection(anchor, part.title, text, audience))
    return tuple(out)


def _page_audience(meta: FrontMatter, source: ManifestSource, where: str) -> Audience:
    audience = meta.audience or source.audience
    if audience is None:
        raise ManifestError(
            f"{where}: audience is required (in the front matter, or on the manifest source)."
        )
    return audience


def markdown_pages(
    text: str,
    *,
    slug: str,
    source: ManifestSource,
    split: str,
    where: str | None = None,
    seen_keys: set[str] | None = None,
) -> list[DocPage]:
    """`slug` is the final page slug for `split="page"` and the prefix for `split="section"`."""
    where = where or source.path
    meta, body = split_front_matter(text, where)
    lines = body.replace("\r\n", "\n").split("\n")
    h1, lines = _first_h1(lines)
    topic = meta.topic or source.topic

    if split == "page":
        audience = _page_audience(meta, source, where)
        title = meta.title or h1 or source.id
        intro, parts = _split(lines, 2, where)
        sections = _sections(
            title, intro, parts, audience, Anchors(), override_source=source, seen=seen_keys
        )
        first = sections[0].text if sections else ""
        return [
            DocPage(
                slug=meta.slug or slug,
                title=title,
                topic=topic,
                audience=audience,
                route=meta.route,
                summary=meta.summary or summary_of(first),
                starters=tuple(meta.starters),
                admin_starters=tuple(meta.admin_starters),
                source_id=source.id,
                public_url=source.public_url,
                sections=sections,
            )
        ]

    # split == "section": one page per `## `
    default = _page_audience(meta, source, where)
    intro, parts = _split(lines, 2, where, inner_level=3)
    pages: list[DocPage] = []
    page_anchors = Anchors()

    intro_text = _clean(intro)
    if len(re.sub(r"[^\w]", "", intro_text)) >= _MEANINGFUL:
        key = "intro"
        over, over_topic = override_for(source, key, seen_keys)
        audience = over or default
        title = h1 or source.id
        pages.append(
            DocPage(
                slug=f"{slug}/{key}",
                title=title,
                topic=over_topic or topic,
                audience=audience,
                route=None,
                summary=summary_of(intro_text),
                starters=(),
                admin_starters=(),
                source_id=source.id,
                public_url=source.public_url,
                sections=(DocSection("", title, intro_text, audience),),
            )
        )
    page_anchors.take("intro")

    for part in parts:
        anchor = page_anchors.take(slugify(part.title))
        over, over_topic = override_for(source, anchor, seen_keys)
        audience = over or part.marker or default
        sub_intro, sub_parts = _split(part.lines, 3, where)
        sections = _sections(
            part.title,
            sub_intro,
            sub_parts,
            audience,
            Anchors(),
            override_source=None,
            seen=None,
        )
        if not sections:
            continue
        pages.append(
            DocPage(
                slug=f"{slug}/{anchor}",
                title=part.title,
                topic=over_topic or topic,
                audience=audience,
                route=None,
                summary=summary_of(sections[0].text),
                starters=(),
                admin_starters=(),
                source_id=source.id,
                public_url=source.public_url,
                sections=sections,
            )
        )
    return pages
