"""Remove page chrome from an HTML file before the parser sees it.

Docling already moves `<nav>`, `<footer>`, `<header>` and cookie banners into its
furniture layer. It leaves `<aside>` and `role=contentinfo` in the body, and it also
treats every paragraph before the page's first heading as furniture, which loses real
lead text. This pass removes the chrome by structure instead, so the parser can then be
told to keep everything that is left.

Removed: `nav`, `header`, `footer`, `aside`; elements with `role` navigation, banner,
contentinfo or complementary; elements whose id or class mentions cookie, consent, gdpr
or onetrust. Never removed: `html`, `body`, anything that contains a `main`, `article`,
`h1` or other heading outright (a cookie class on a wrapper must not delete the page),
and a `header` or `footer` inside an `article` or `main` (those belong to the article,
not the page).

The result is returned only when at least 200 characters of text are left; a page that
is nothing but chrome is better parsed as it was.
"""

import logging
import re

logger = logging.getLogger(__name__)

MIN_BODY_CHARS = 200

_CHROME_TAGS = {"nav", "header", "footer", "aside"}
_CHROME_ROLES = {"navigation", "banner", "contentinfo", "complementary"}
_CHROME_NAME = re.compile(r"cookie|consent|gdpr|onetrust", re.IGNORECASE)
_NEVER_REMOVE = {"html", "body", "head"}
_CONTENT_TAGS = ("main", "article", "h1", "h2", "h3", "h4", "h5", "h6")
_SCOPED_TO_ARTICLE = {"header", "footer"}
_SECTIONING = {"article", "main"}


def _is_chrome(el) -> bool:  # type: ignore[no-untyped-def]
    tag = el.tag if isinstance(el.tag, str) else ""
    if tag in _NEVER_REMOVE or not tag:
        return False
    if tag in _CHROME_TAGS:
        # A header or footer inside an article belongs to the article, not to the page.
        inside_article = any(getattr(a, "tag", None) in _SECTIONING for a in el.iterancestors())
        return not (tag in _SCOPED_TO_ARTICLE and inside_article)
    if (el.get("role") or "").strip().lower() in _CHROME_ROLES:
        return True
    name = f"{el.get('id', '')} {el.get('class', '')}"
    if name.strip() and _CHROME_NAME.search(name):
        # A wrapper that holds the article is not a cookie banner, whatever it is called.
        return not any(True for _ in el.iterdescendants(*_CONTENT_TAGS))
    return False


def _body_text_length(root) -> int:  # type: ignore[no-untyped-def]
    body = root.find("body")
    node = body if body is not None else root
    return len(" ".join(node.text_content().split()))


def prestrip_html(raw: bytes) -> bytes | None:
    """The page as UTF-8 HTML without its chrome, or None when nothing should change.

    None means: the pass found nothing to remove, the input is not HTML, or removing the
    chrome would leave under 200 characters of text. The caller parses the original.
    """
    if not raw or not raw.strip():
        return None
    try:
        import lxml.html

        try:
            raw.decode("utf-8")
            parser = lxml.html.HTMLParser(encoding="utf-8")
        except UnicodeDecodeError:
            parser = lxml.html.HTMLParser()  # honour the page's own charset declaration
        root = lxml.html.fromstring(raw, parser=parser)
    except Exception:
        logger.debug("HTML pre-pass: not parseable, left as it is", exc_info=True)
        return None

    for el in list(root.iter("script", "style", "noscript", "template")):
        el.drop_tree()

    doomed = [el for el in root.iter() if _is_chrome(el)]
    if not doomed:
        return None
    for el in doomed:
        if el.getparent() is not None:  # an ancestor may have taken it already
            el.drop_tree()
    if _body_text_length(root) < MIN_BODY_CHARS:
        return None

    head = root.find("head")
    if head is not None and not any(
        (m.get("charset") or "utf-8").lower() == "utf-8" and m.get("charset")
        for m in head.iter("meta")
    ):
        meta = lxml.html.Element("meta")
        meta.set("charset", "utf-8")
        head.insert(0, meta)
    out: bytes = lxml.html.tostring(root, encoding="utf-8", doctype="<!DOCTYPE html>")
    return out
