"""P3 T2: the legal Markdown renderer cannot emit markup we did not make, and the shipped
templates are marked, generic and use only known placeholders."""

import random
from html.parser import HTMLParser

import pytest

from callobuzz_rag.legal.render import PLACEHOLDERS, fill, to_html, used_placeholders
from callobuzz_rag.legal.store import SLUGS, default_template

HOSTILE = [
    "<script>alert(1)</script>",
    "[x](javascript:alert(1))",
    "[x](JaVaScRiPt:alert(1))",
    "[x](data:text/html;base64,PHNjcmlwdD4=)",
    "![i](javascript:alert(1))",
    '<img src=x onerror="alert(1)">',
    "<a href='javascript:alert(1)'>x</a>",
    "[x](vbscript:msgbox(1))",
    "<iframe src=https://evil.example></iframe>",
    "<svg onload=alert(1)>",
    "[x](&#106;avascript:alert(1))",
    "[x](java\tscript:alert(1))",
    "[x](//evil.example/x)",
]

ALLOWED_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "em", "strong", "code",
    "pre", "blockquote", "a", "hr", "br", "table", "thead", "tbody", "tr", "th", "td", "img",
}  # fmt: skip
SAFE_PREFIXES = ("http://", "https://", "mailto:", "tel:", "#", "/")


class _Collect(HTMLParser):
    """What a browser would build: the elements and their attributes."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()
        self.attrs: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)
        for name, value in attrs:
            self.attrs.append((tag, name, value or ""))


def _assert_inert(html: str) -> None:
    parser = _Collect()
    parser.feed(html)
    assert parser.tags <= ALLOWED_TAGS, parser.tags
    for tag, name, value in parser.attrs:
        assert not name.startswith("on"), (tag, name)
        assert tag != "a" or name in {"href", "rel", "title"}, (tag, name)
        assert tag != "img" or name in {"src", "alt", "title"}, (tag, name)
        if name in {"href", "src"}:
            assert value.lower().startswith(SAFE_PREFIXES), value
            assert not value.startswith("//"), value
        if name == "rel":
            assert value == "nofollow noopener"


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_markdown_renders_inert(text: str) -> None:
    _assert_inert(to_html(text))


@pytest.mark.parametrize("seed", range(200))
def test_random_input_never_yields_markup_we_did_not_make(seed: int) -> None:
    rng = random.Random(seed)
    alphabet = "<>\"'=&/()[]!#*`:-_ \nabcjavascriptonloadscriptiframe"
    _assert_inert(to_html("".join(rng.choice(alphabet) for _ in range(rng.randint(0, 400)))))


def test_links_get_rel_and_safe_schemes_survive() -> None:
    html = to_html(
        "[mail](mailto:grievance@sharmafurniture.in) [site](https://sharmafurniture.in) [top](#a)"
    )
    assert html.count('rel="nofollow noopener"') == 3
    assert 'href="mailto:grievance@sharmafurniture.in"' in html


def test_unsafe_link_is_not_an_anchor() -> None:
    assert "<a " not in to_html("[x](javascript:alert(1))")


def test_tables_render() -> None:
    html = to_html("| Name | Country |\n|---|---|\n| Hosting | India |\n")
    assert "<table>" in html and "<td>India</td>" in html


def test_fill_uses_replace_not_format_and_marks_unknown() -> None:
    out = fill("{{legal_name}} {0} {{nope}} {product_name}", {"legal_name": "Sharma Furniture"})
    assert out == "Sharma Furniture {0} [[nope]] {product_name}"


def test_fill_does_not_expand_inside_values() -> None:
    out = fill("{{legal_name}} {{city}}", {"legal_name": "{{city}}", "city": "Pune"})
    assert out == "{{city}} Pune"


@pytest.mark.parametrize("slug", SLUGS)
def test_every_template_is_marked_and_uses_only_known_placeholders(slug: str) -> None:
    text = default_template(slug)
    assert text.startswith(
        "[//]: # (TEMPLATE — have your lawyer review this page before you publish it.)"
    )
    assert "TEMPLATE" not in to_html(text)  # the marker renders to nothing
    assert used_placeholders(text) <= set(PLACEHOLDERS)
    lower = text.lower()
    assert "callobuzz" not in lower and "codecanyon" not in lower
    assert "\n# " in text
    _assert_inert(to_html(text))


def test_privacy_template_names_dpdp_roles_and_rights() -> None:
    text = default_template("privacy").lower()
    for phrase in (
        "digital personal data protection act, 2023",
        "data fiduciary",
        "data processor",
        "grievance",
        "90 days",
        "data protection board of india",
    ):
        assert phrase in text


def test_refund_template_has_concrete_day_counts() -> None:
    text = default_template("refunds").lower()
    assert "7 working days" in text and "at our discretion" not in text
