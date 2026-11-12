"""The hand-written HTML pages (`guide.html`, `deploy.html`, `benchmarks.html`) to `DocPage`s.

Standard-library `HTMLParser` into a small Markdown subset (h2-h4, p, lists, pre/code, tables,
links, bold, italic). One page per top-level `<section id>`; its first `<h2>` is the page title,
each `<h3>` starts a section (a retrieval record). Scripts, styles, navigation, buttons, icons
and anything `aria-hidden` are dropped.
"""

import re
from html.parser import HTMLParser
from typing import Any

from callobuzz_rag.cob.manifest import ManifestError, ManifestSource
from callobuzz_rag.cob.sources.common import Anchors, override_for, slugify, summary_of
from callobuzz_rag.cob.types import Audience, DocPage, DocSection

_SKIP_TAGS = frozenset({"script", "style", "nav", "header", "footer", "svg", "button", "noscript"})
_SKIP_CLASSES = frozenset({"anchor", "cb-bar", "toc", "sr-only", "visually-hidden"})
_VOID = frozenset({"br", "hr", "img", "meta", "link", "input", "source", "wbr", "col", "area"})
_BLOCK_TAGS = frozenset(
    {
        "p", "div", "section", "article", "aside", "main", "blockquote", "details", "summary",
        "figure", "figcaption", "dl", "dt", "dd", "ul", "ol", "tr", "form", "fieldset",
    }
)  # fmt: skip
_WS = re.compile(r"\s+")

# A block of converted content: ("h", level, id, text), ("li", line) or ("md", text).
Block = tuple[Any, ...]


def _collapse(text: str) -> str:
    return _WS.sub(" ", text).strip()


class _Capture:
    def __init__(self, section_id: str) -> None:
        self.id = section_id
        self.blocks: list[Block] = []


class _Converter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.captures: list[_Capture] = []
        self._cur: _Capture | None = None
        self._section_depth = 0
        self._stack: list[str] = []
        self._skip_at: int | None = None
        self._inline: list[str] = []
        self._in_pre = False
        self._pre: list[str] = []
        self._heading: tuple[int, str] | None = None
        self._lists: list[list[Any]] = []  # [kind, counter]
        self._item_prefix: str | None = None
        self._list_id = 0
        self._links: list[bool] = []
        self._table: list[list[tuple[str, bool]]] | None = None
        self._row: list[tuple[str, bool]] | None = None
        self._cell_th = False
        self._in_cell = False
        self._has_thead = False

    # --- helpers -------------------------------------------------------------------------

    @property
    def _skipping(self) -> bool:
        return self._skip_at is not None

    def _emit(self, block: Block) -> None:
        if self._cur is not None:
            self._cur.blocks.append(block)

    def _flush(self) -> None:
        """End the current paragraph or list line."""
        if self._in_cell:
            self._inline.append(" ")
            return
        text = _collapse("".join(self._inline))
        self._inline = []
        if self._heading is not None:
            return
        if not text:
            return
        if self._item_prefix is not None:
            self._emit(("li", self._item_prefix + text, self._list_id))
            self._item_prefix = None
        elif self._lists:
            self._emit(("li", "  " * len(self._lists) + text, self._list_id))
        else:
            self._emit(("md", text))

    def _should_skip(self, tag: str, attrs: dict[str, str | None]) -> bool:
        if tag in _SKIP_TAGS or attrs.get("aria-hidden") == "true" or "hidden" in attrs:
            return True
        classes = set((attrs.get("class") or "").split())
        if classes & _SKIP_CLASSES:
            return True
        return bool(self._heading is not None and "n" in classes)

    # --- parser callbacks ------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = dict(attrs_list)
        if tag in _VOID:
            if not self._skipping and tag == "br":
                self._inline.append(" ")
            return
        self._stack.append(tag)
        if self._skipping:
            return
        if self._should_skip(tag, attrs):
            self._skip_at = len(self._stack)
            if not self._in_pre:
                self._inline.append(" ")
            return

        if tag == "section":
            self._section_depth += 1
            if self._section_depth == 1 and attrs.get("id"):
                self._flush()
                self._inline = []
                self._cur = _Capture(str(attrs["id"]))
                self.captures.append(self._cur)
            return
        if self._cur is None:
            return

        if self._in_pre:
            return
        if tag == "pre":
            self._flush()
            self._in_pre = True
            self._pre = []
        elif tag in ("h2", "h3", "h4"):
            self._flush()
            self._heading = (int(tag[1]), str(attrs.get("id") or ""))
            self._inline = []
        elif tag in ("ul", "ol"):
            self._flush()
            if not self._lists:
                self._list_id += 1
            self._lists.append([tag, 0])
        elif tag == "li":
            self._flush()
            if self._lists:
                kind = self._lists[-1][0]
                self._lists[-1][1] += 1
                marker = "-" if kind == "ul" else f"{self._lists[-1][1]}."
                self._item_prefix = "  " * (len(self._lists) - 1) + marker + " "
        elif tag == "table":
            self._flush()
            self._table = []
            self._has_thead = False
        elif tag == "thead":
            self._has_thead = True
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._in_cell = True
            self._cell_th = tag == "th"
            self._inline = []
        elif tag == "code":
            self._inline.append("`")
        elif tag in ("strong", "b", "dt"):
            self._inline.append("**")
        elif tag in ("em", "i"):
            self._inline.append("*")
        elif tag == "a":
            href = (attrs.get("href") or "").strip()
            keep = bool(href) and not href.startswith(("#", "javascript:", "mailto:"))
            self._links.append(keep)
            if keep:
                self._inline.append("[")
                self._href = href
        if tag in _BLOCK_TAGS and tag != "dt":
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID or tag not in self._stack:
            return
        index = len(self._stack) - 1 - self._stack[::-1].index(tag)
        closing = self._stack[index:]
        del self._stack[index:]
        if self._skip_at is not None:
            if len(self._stack) < self._skip_at:
                self._skip_at = None
            return
        for closed in reversed(closing):
            self._close(closed)

    def _close(self, tag: str) -> None:
        if tag == "section":
            self._section_depth = max(0, self._section_depth - 1)
            if self._section_depth == 0 and self._cur is not None:
                self._flush()
                self._cur = None
            return
        if self._cur is None:
            return
        if self._in_pre:
            if tag == "pre":
                code = "".join(self._pre).strip("\n")
                self._in_pre = False
                if code.strip():
                    self._emit(("md", f"```\n{code}\n```"))
            return
        if tag in ("h2", "h3", "h4") and self._heading is not None:
            level, hid = self._heading
            text = _collapse("".join(self._inline))
            self._heading = None
            self._inline = []
            if text:
                self._emit(("h", level, hid, text))
        elif tag in ("ul", "ol"):
            self._flush()
            if self._lists:
                self._lists.pop()
        elif tag == "li":
            self._flush()
            self._item_prefix = None
        elif tag in ("td", "th") and self._in_cell and self._row is not None:
            cell = _collapse("".join(self._inline)).replace("|", "\\|")
            self._row.append((cell, self._cell_th))
            self._inline = []
            self._in_cell = False
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            rendered = _render_table(self._table)
            self._table = None
            self._row = None
            self._in_cell = False
            if rendered:
                self._emit(("md", rendered))
        elif tag == "code":
            self._inline.append("`")
        elif tag in ("strong", "b", "dt"):
            self._inline.append("**")
            if tag == "dt":
                self._flush()
        elif tag in ("em", "i"):
            self._inline.append("*")
        elif tag == "a" and self._links and self._links.pop():
            self._inline.append(f"]({self._href})")
        if tag in _BLOCK_TAGS and tag != "dt":
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skipping or self._cur is None:
            return
        if self._in_pre:
            self._pre.append(data)
        else:
            self._inline.append(data)

    _href: str = ""


def _render_table(rows: list[list[tuple[str, bool]]]) -> str:
    width = max((len(r) for r in rows), default=0)
    if width == 0:
        return ""
    cells = [[c for c, _ in r] + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(cells[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in cells[1:]]
    return "\n".join(lines)


def _render(blocks: list[Block]) -> str:
    out: list[str] = []
    previous: tuple[str, int] = ("", 0)
    for block in blocks:
        kind = block[0]
        list_id = 0
        if kind == "h":
            text = "#" * block[1] + " " + block[3]
            kind = "md"
        else:
            text = block[1]
            if kind == "li":
                list_id = block[2]
        if out:
            out.append("\n" if kind == "li" and previous == ("li", list_id) else "\n\n")
        out.append(text)
        previous = (kind, list_id)
    return "".join(out).strip()


def html_pages(
    html: str,
    *,
    slug: str,
    source: ManifestSource,
    seen_keys: set[str] | None = None,
) -> list[DocPage]:
    """One page per top-level `<section id>`: slug `<slug>/<id without 'sec-'>`."""
    converter = _Converter()
    converter.feed(html)
    converter.close()

    pages: list[DocPage] = []
    page_anchors = Anchors()
    for capture in converter.captures:
        blocks = capture.blocks
        title_index = next((i for i, b in enumerate(blocks) if b[0] == "h" and b[1] == 2), None)
        if title_index is not None:
            title, title_id = blocks[title_index][3], blocks[title_index][2]
            blocks = blocks[:title_index] + blocks[title_index + 1 :]
        else:
            title, title_id = capture.id, ""
        key = capture.id
        over_audience, over_topic = override_for(source, key, seen_keys)
        audience = over_audience or source.audience
        if audience is None:
            raise ManifestError(
                f"{source.path}: section '{key}' has no audience (set one on the source)."
            )
        sections = _html_sections(title, blocks, audience)
        if not sections:
            continue
        anchor = page_anchors.take(slugify(key.removeprefix("sec-")))
        public_url = f"{source.public_url}#{title_id or key}" if source.public_url else None
        pages.append(
            DocPage(
                slug=f"{slug}/{anchor}",
                title=title,
                topic=over_topic or source.topic,
                audience=audience,
                route=None,
                summary=summary_of(sections[0].text),
                starters=(),
                admin_starters=(),
                source_id=source.id,
                public_url=public_url,
                sections=sections,
            )
        )
    return pages


def _html_sections(title: str, blocks: list[Block], audience: Audience) -> tuple[DocSection, ...]:
    groups: list[tuple[str, str, list[Block]]] = [("", title, [])]
    for block in blocks:
        if block[0] == "h" and block[1] == 3:
            groups.append((block[2] or slugify(block[3]), block[3], []))
        else:
            groups[-1][2].append(block)
    anchors = Anchors()
    out: list[DocSection] = []
    for anchor, section_title, group in groups:
        text = _render(group)
        if not text:
            continue
        final = anchors.take(anchor) if anchor else ""
        out.append(DocSection(final, section_title, text, audience))
    return tuple(out)
