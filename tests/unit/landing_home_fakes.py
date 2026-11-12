"""Helpers for the home page tests (LP2): render the home page of a content document straight to
HTML, and read it back as a small tree (no browser, no database, no running engine)."""

import re
from html.parser import HTMLParser
from typing import Any

from callobuzz_rag.landing import render
from callobuzz_rag.landing.schema import LandingContent
from callobuzz_rag.landing.store import theme_of
from callobuzz_rag.landing.theme import theme_css
from tests.unit.landing_fakes import content_with, make_env


def render_home(content: LandingContent | None = None, *, lang: str = "en", **env: Any) -> str:
    content = content or content_with()
    tokens = theme_of(content)
    ctx = render.PageContext(
        content=content,
        page="home",
        lang=lang,
        theme_css=theme_css(tokens, content.theme.base),
        env=make_env(**env),
    )
    return render.render_page(ctx).html


class _Node:
    def __init__(self, tag: str, attrs: dict[str, str], parent: "_Node | None") -> None:
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[_Node] = []
        self.text: list[str] = []

    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    def walk(self) -> "list[_Node]":
        out = [self]
        for child in self.children:
            out += child.walk()
        return out

    def all_text(self) -> str:
        return " ".join(part.strip() for node in self.walk() for part in node.text if part.strip())

    def find_all(self, tag: str | None = None, cls: str | None = None) -> "list[_Node]":
        return [
            n
            for n in self.walk()
            if (tag is None or n.tag == tag) and (cls is None or cls in n.classes())
        ]

    def ancestors(self) -> "list[_Node]":
        out: list[_Node] = []
        node = self.parent
        while node is not None:
            out.append(node)
            node = node.parent
        return out


_VOID = {"meta", "link", "br", "img", "input", "hr", "source", "path", "circle", "rect"}


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {k: v or "" for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.cur.children.append(_Node(tag, {k: v or "" for k, v in attrs}, self.cur))

    def handle_endtag(self, tag: str) -> None:
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent or self.root
        if node is not self.root:
            self.cur = node.parent or self.root

    def handle_data(self, data: str) -> None:
        self.cur.text.append(data)


def parse(html: str) -> _Node:
    tree = _Tree()
    tree.feed(html)
    return tree.root


def section_texts(html: str, *, exclude_selector: str | None = None) -> list[str]:
    """The visible text of every `<section data-section=...>`, leaving out elements with a
    class (`exclude_selector=".contact-link"`)."""
    exclude = exclude_selector.lstrip(".") if exclude_selector else None
    out: list[str] = []
    for section in parse(html).find_all("section"):
        if "data-section" not in section.attrs:
            continue
        parts: list[str] = []
        for node in section.walk():
            if exclude and any(exclude in n.classes() for n in [node, *node.ancestors()]):
                continue
            parts += [p.strip() for p in node.text if p.strip()]
        out.append(" ".join(parts))
    return out


def section_order(html: str) -> list[str]:
    return re.findall(r'<section [^>]*data-section="([a-z_]+)"', html)
