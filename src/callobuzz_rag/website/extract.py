"""Page extraction for website learning (BIZ1): HTML in, Markdown text, links and flags out.

Links are collected first, from the whole page, with the region they sit in (the ranking wants
to know whether the owner put a page in the menu or the footer). Then the page chrome goes
(`parsing.html_prestrip`), then everything that is not content, and what is left becomes
Markdown: headings as `#` lines (they feed the chunker's heading splits), list items as `- `,
table rows as `a | b`. No new dependency: lxml is already installed.
"""

import contextlib
import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Final, Literal
from urllib.parse import urlsplit

import lxml.html
from lxml import etree

from callobuzz_rag.parsing.html_prestrip import prestrip_html
from callobuzz_rag.website.urls import canonical

logger = logging.getLogger(__name__)

Region = Literal["nav", "footer", "body"]

FILE_EXTENSIONS: Final = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".csv")
_DROP_TAGS: Final = ("script", "style", "noscript", "template", "svg", "form", "iframe", "button")
_COOKIE_NAME = re.compile(r"cookie|consent|gdpr|onetrust", re.IGNORECASE)
_CONTENT_TAGS: Final = ("main", "article", "h1", "h2", "h3", "h4", "h5", "h6")
_HEADINGS: Final = {f"h{n}": n for n in range(1, 7)}
_BLOCKS: Final = frozenset(
    {
        "p", "div", "section", "article", "main", "ul", "ol", "li", "table", "tr", "thead",
        "tbody", "tfoot", "blockquote", "pre", "dl", "dt", "dd", "figure", "figcaption",
        "address", "body", "details", "summary", "header", "footer", "nav", "aside",
        *_HEADINGS,
    }
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Link:
    url: str  # canonical
    region: Region
    nofollow: bool = False


@dataclass(frozen=True)
class Extracted:
    title: str
    markdown: str
    links: list[Link] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    noindex: bool = False
    nofollow: bool = False
    canonical_url: str | None = None
    content_hash: str = ""
    words: int = 0


def _squash(text: str) -> str:
    return " ".join(text.split())


def _robots_meta(root: lxml.html.HtmlElement) -> tuple[bool, bool]:
    noindex = nofollow = False
    for meta in root.iter("meta"):
        if (meta.get("name") or "").strip().lower() != "robots":
            continue
        tokens = {t.strip().lower() for t in (meta.get("content") or "").split(",")}
        noindex = noindex or "noindex" in tokens or "none" in tokens
        nofollow = nofollow or "nofollow" in tokens or "none" in tokens
    return noindex, nofollow


def _title(root: lxml.html.HtmlElement) -> str:
    node = root.find(".//title")
    if node is not None and _squash(node.text_content()):
        return _squash(node.text_content())[:500]
    for meta in root.iter("meta"):
        if (meta.get("property") or "").lower() == "og:title" and (
            meta.get("content") or ""
        ).strip():
            return _squash(meta.get("content") or "")[:500]
    h1 = root.find(".//h1")
    if h1 is not None:
        return _squash(h1.text_content())[:500]
    return ""


def _region(anchor: lxml.html.HtmlElement) -> Region:
    for ancestor in anchor.iterancestors():
        tag = ancestor.tag if isinstance(ancestor.tag, str) else ""
        if tag in ("nav", "header"):
            return "nav"
        if tag == "footer":
            return "footer"
        if (ancestor.get("role") or "").lower() == "navigation":
            return "nav"
        if (ancestor.get("role") or "").lower() == "contentinfo":
            return "footer"
    return "body"


def _links(root: lxml.html.HtmlElement, base: str) -> tuple[list[Link], list[str]]:
    links: dict[str, Link] = {}
    files: dict[str, None] = {}
    for anchor in root.iter("a"):
        href = (anchor.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        url = canonical(href, base=base)
        if url is None:
            continue
        if urlsplit(url).path.lower().endswith(FILE_EXTENSIONS):
            files.setdefault(url, None)
            continue
        nofollow = "nofollow" in (anchor.get("rel") or "").lower().split()
        region = _region(anchor)
        old = links.get(url)
        if old is None:
            links[url] = Link(url, region, nofollow)
        elif old.region == "body" and region != "body":
            links[url] = Link(url, region, old.nofollow and nofollow)
        elif old.nofollow and not nofollow:
            links[url] = Link(url, old.region, False)
    return list(links.values()), list(files)


def _drop(el: lxml.html.HtmlElement) -> None:
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail
    if tail:
        previous = el.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(el)


def _strip_non_content(root: lxml.html.HtmlElement) -> None:
    for el in list(root.iter(*_DROP_TAGS)):
        _drop(el)
    for el in list(root.iter(etree.Comment, etree.ProcessingInstruction)):
        _drop(el)
    for el in list(root.iter()):
        if not isinstance(el.tag, str) or el.tag in ("html", "body", "head"):
            continue
        name = f"{el.get('id', '')} {el.get('class', '')}"
        is_banner = bool(name.strip() and _COOKIE_NAME.search(name))
        if is_banner and not any(True for _ in el.iterdescendants(*_CONTENT_TAGS)):
            _drop(el)


def _inline(el: lxml.html.HtmlElement) -> str:
    return _squash(" ".join(el.itertext()))


def _walk(el: lxml.html.HtmlElement, out: list[str]) -> None:
    tag = el.tag if isinstance(el.tag, str) else ""
    if tag in _HEADINGS:
        text = _inline(el)
        if text:
            out.append("#" * _HEADINGS[tag] + " " + text)
        return
    if tag == "pre":
        text = "\n".join(line.rstrip() for line in (el.text_content() or "").strip().splitlines())
        if text:
            out.append(text)
        return
    if tag == "tr":
        cells = [_inline(c) for c in el if isinstance(c.tag, str) and c.tag in ("td", "th")]
        row = " | ".join(c for c in cells if c)
        if row:
            out.append(row)
        return
    if tag == "li":
        _flow(el, out, bullet=True)
        return
    _flow(el, out, bullet=False)


def _flow(el: lxml.html.HtmlElement, out: list[str], *, bullet: bool) -> None:
    """Text of one element as lines: loose text stays together, nested blocks are walked in order."""
    pieces: list[tuple[str, lxml.html.HtmlElement | str]] = []
    buffer = [el.text or ""]
    for child in el:
        tag = child.tag if isinstance(child.tag, str) else ""
        if tag in _BLOCKS:
            pieces.append(("text", "".join(buffer)))
            pieces.append(("node", child))
            buffer = [child.tail or ""]
        else:
            buffer.append(" " + _inline(child) + " " + (child.tail or ""))
    pieces.append(("text", "".join(buffer)))
    if bullet:
        text = " ".join(t for t in (_squash(str(v)) for k, v in pieces if k == "text") if t)
        if text:
            out.append("- " + text)
        for kind, value in pieces:
            if kind == "node":
                _walk(value, out)
        return
    for kind, value in pieces:
        if kind == "text":
            text = _squash(str(value))
            if text:
                out.append(text)
        else:
            _walk(value, out)


def _markdown(root: lxml.html.HtmlElement) -> str:
    body = root.find("body")
    lines: list[str] = []
    _walk(body if body is not None else root, lines)
    # Consecutive list items / table rows form one paragraph; everything else is its own.
    blocks: list[str] = []
    previous = ""
    for line in lines:
        if line == previous:
            continue
        previous = line
        is_item = line.startswith("- ") or " | " in line
        if is_item and blocks and not blocks[-1].startswith("#") and _is_item_block(blocks[-1]):
            blocks[-1] += "\n" + line
        else:
            blocks.append(line)
    return "\n\n".join(blocks).strip()


def _is_item_block(block: str) -> bool:
    first = block.split("\n", 1)[0]
    return first.startswith("- ") or " | " in first


def extract_page(html: bytes | str, url: str) -> Extracted:
    """Everything website learning needs from one HTML page. Never raises on bad markup."""
    raw = html.encode("utf-8") if isinstance(html, str) else html
    if not raw.strip():
        return Extracted(title="", markdown="")
    try:
        parser = lxml.html.HTMLParser(encoding="utf-8") if isinstance(html, str) else None
        root = lxml.html.document_fromstring(raw, parser=parser)
    except (etree.ParserError, ValueError):
        logger.debug("page is not parseable HTML: %s", url)
        return Extracted(title="", markdown="")

    noindex, nofollow = _robots_meta(root)
    canon: str | None = None
    for link in root.iter("link"):
        if "canonical" in (link.get("rel") or "").lower().split() and link.get("href"):
            canon = canonical(link.get("href") or "", base=url)
            break
    title = _title(root)
    links, files = _links(root, url)

    stripped = prestrip_html(raw)
    if stripped is not None:
        with contextlib.suppress(etree.ParserError, ValueError):
            root = lxml.html.document_fromstring(
                stripped, parser=lxml.html.HTMLParser(encoding="utf-8")
            )
    _strip_non_content(root)
    markdown = _markdown(root)
    plain = _squash(markdown.replace("#", " ").replace("|", " "))
    return Extracted(
        title=title,
        markdown=markdown,
        links=links,
        files=files,
        noindex=noindex,
        nofollow=nofollow,
        canonical_url=canon,
        content_hash=hashlib.sha256(_squash(markdown).encode("utf-8")).hexdigest(),
        words=len(plain.split()),
    )
