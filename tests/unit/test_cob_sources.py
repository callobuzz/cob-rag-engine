"""COB's source loaders: HTML sections to Markdown, Markdown page and section splits."""

import pytest

from callobuzz_rag.cob.manifest import ManifestError, ManifestSource
from callobuzz_rag.cob.sources.common import slugify
from callobuzz_rag.cob.sources.html import html_pages
from callobuzz_rag.cob.sources.markdown import markdown_pages
from callobuzz_rag.cob.types import Audience


def source(**kw: object) -> ManifestSource:
    base: dict[str, object] = {
        "id": "guide",
        "path": "docs/guide.html",
        "kind": "html",
        "split": "section",
        "slug": "guide",
        "topic": "getting-started",
        "audience": "admin",
        "public_url": "https://example.test/guide.html",
    }
    base.update(kw)
    return ManifestSource.model_validate(base)


def test_html_section_split_drops_chrome() -> None:
    html = (
        "<nav>menu</nav>"
        '<section class="s" id="sec-flow"><h2>Try the flow</h2><p>Step <b>one</b></p>'
        "<script>x()</script><pre><code>make up</code></pre></section>"
    )
    pages = html_pages(html, slug="guide", source=source())
    assert pages[0].slug == "guide/flow" and pages[0].title == "Try the flow"
    text = pages[0].sections[0].text
    assert "menu" not in text and "x()" not in text
    assert "Step **one**" in text
    assert "```\nmake up\n```" in text
    assert pages[0].public_url == "https://example.test/guide.html#sec-flow"


def test_html_headings_numbers_buttons_and_hidden_are_dropped() -> None:
    html = """
    <section id="sec-a"><h2 id="a"><span class="n">01</span>Title<a class="anchor" href="#a">#</a></h2>
    <p>Intro <a href="https://x.test/p">link</a> and <a href="#top">top</a>.</p>
    <button class="copy">Copy</button><div aria-hidden="true">ghost</div>
    <h3 id="part-one"><span class="n">1.1</span>Part one</h3>
    <ul><li>first</li><li>second <code>x</code><ul><li>nested</li></ul></li></ul>
    <ol><li>one</li><li>two</li></ol>
    <h3>Part one</h3><p>again</p>
    </section>"""
    page = html_pages(html, slug="guide", source=source())[0]
    assert page.title == "Title"
    assert page.public_url == "https://example.test/guide.html#a"
    assert [s.anchor for s in page.sections] == ["", "part-one", "part-one-2"]
    intro, part, again = page.sections
    assert intro.text == "Intro [link](https://x.test/p) and top."
    assert "Copy" not in part.text and "ghost" not in "".join(s.text for s in page.sections)
    assert part.text == "- first\n- second `x`\n  - nested\n\n1. one\n2. two"
    assert part.title == "Part one" and again.text == "again"


def test_html_table_becomes_markdown() -> None:
    html = (
        '<section id="t"><h2>T</h2><table><thead><tr><th>Name</th><th>What</th></tr></thead>'
        "<tbody><tr><td>a|b</td><td><code>x</code></td></tr></tbody></table></section>"
    )
    text = html_pages(html, slug="s", source=source())[0].sections[0].text
    assert text == "| Name | What |\n|---|---|\n| a\\|b | `x` |"


def test_html_section_override_sets_audience_and_topic() -> None:
    html = '<section id="sec-overview"><h2>Overview</h2><p>Hello there friend.</p></section>'
    src = source(sections={"sec-overview": {"audience": "public", "topic": "assistant"}})
    seen: set[str] = set()
    page = html_pages(html, slug="guide", source=src, seen_keys=seen)[0]
    assert page.audience == Audience.PUBLIC and page.topic == "assistant"
    assert page.sections[0].audience == Audience.PUBLIC and seen == {"sec-overview"}


README = """# Project

Badges only.

## Quick start

Do this first.

```bash
## not a heading
make up
```

### Details

More here.

### Secrets
<!-- audience: admin -->
Hidden line.

## Plans and usage caps

Four limits.
"""


def test_markdown_section_split_makes_a_page_per_h2() -> None:
    src = source(
        id="readme",
        path="README.md",
        kind="markdown",
        slug="readme",
        audience="public",
        sections={"quick-start": {"audience": "admin"}},
    )
    pages = markdown_pages(README, slug="readme", source=src, split="section")
    assert [p.slug for p in pages] == ["readme/quick-start", "readme/plans-and-usage-caps"]
    quick = pages[0]
    assert quick.audience == Audience.ADMIN
    assert [s.anchor for s in quick.sections] == ["", "details", "secrets"]
    assert "## not a heading" in quick.sections[0].text
    assert pages[1].audience == Audience.PUBLIC


def test_markdown_h2_marker_sets_the_page_audience() -> None:
    text = "## Ops\n<!-- audience: admin -->\nRun it.\n\n## Use\nUse it.\n"
    src = source(id="r", path="R.md", kind="markdown", slug="r", audience="customer")
    pages = markdown_pages(text, slug="r", source=src, split="section")
    assert [(p.slug, p.audience) for p in pages] == [
        ("r/ops", Audience.ADMIN),
        ("r/use", Audience.CUSTOMER),
    ]


def test_slugify_matches_github() -> None:
    assert (
        slugify("Running the API natively (development)") == "running-the-api-natively-development"
    )
    assert slugify("The operator's own workspace") == "the-operators-own-workspace"
    assert slugify("Assistants, leads and handoff") == "assistants-leads-and-handoff"
    assert slugify("`GET /api/v1/admin/tenants`") == "get-apiv1admintenants"


def test_a_marker_after_a_blank_line_is_an_error_not_a_silent_leak() -> None:
    text = "## Ops\n\n<!-- audience: admin -->\nRun it.\n"
    src = source(id="r", path="R.md", kind="markdown", slug="r", audience="customer")
    for split in ("page", "section"):
        with pytest.raises(ManifestError, match="audience marker"):
            markdown_pages(text, slug="r", source=src, split=split)


def test_a_marker_under_h3_on_a_page_split_is_an_error() -> None:
    text = "## Ops\nText.\n\n### Deep\n<!-- audience: admin -->\nSecret.\n"
    src = source(id="r", path="R.md", kind="markdown", slug="r", audience="customer")
    with pytest.raises(ManifestError, match="audience marker"):
        markdown_pages(text, slug="r", source=src, split="page")
    pages = markdown_pages(text, slug="r", source=src, split="section")  # allowed here
    assert [s.audience for s in pages[0].sections] == [Audience.CUSTOMER, Audience.ADMIN]


def test_a_marker_in_the_intro_is_an_error_but_not_inside_a_code_fence() -> None:
    src = source(id="r", path="R.md", kind="markdown", slug="r", audience="customer")
    intro = "<!-- audience: admin -->\nIntro.\n\n## A\nx\n"
    with pytest.raises(ManifestError, match="audience marker"):
        markdown_pages(intro, slug="r", source=src, split="page")
    fenced = "## A\n```\n<!-- audience: admin -->\n```\n"
    assert markdown_pages(fenced, slug="r", source=src, split="page")
