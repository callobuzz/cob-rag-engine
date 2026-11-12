"""The Docs service: the tree, a page and search for one role, built from the real docs."""

import pytest

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.cob import docs_service
from callobuzz_rag.cob.kb import load_docs_index
from callobuzz_rag.cob.types import CobRole, allowed_audiences

OWNER = allowed_audiences(CobRole.OWNER)
ADMIN = allowed_audiences(CobRole.ADMIN)


@pytest.fixture(scope="module")
def index():  # type: ignore[no-untyped-def]
    return load_docs_index()


def test_tree_hides_admin_pages_and_empty_topics_for_an_owner(index) -> None:  # type: ignore[no-untyped-def]
    tree = docs_service.tree_for(index, OWNER)
    slugs = {p.slug for t in tree.topics for p in t.pages}
    assert "screens/widget" in slugs and "screens/engine" not in slugs
    assert not any(s.startswith("settings/") for s in slugs)
    assert all(t.id not in {"settings", "operations"} for t in tree.topics)
    assert all(t.pages for t in tree.topics)
    assert tree.version == index.version


def test_the_admin_tree_has_the_admin_topics(index) -> None:  # type: ignore[no-untyped-def]
    tree = docs_service.tree_for(index, ADMIN)
    assert {"settings", "screens"} <= {t.id for t in tree.topics}


def test_page_drops_admin_sections_for_an_owner(index) -> None:  # type: ignore[no-untyped-def]
    owner = docs_service.page_for(index, "screens/overview", OWNER)
    admin = docs_service.page_for(index, "screens/overview", ADMIN)
    assert "Sign-ups waiting" not in owner.markdown and "Sign-ups waiting" in admin.markdown
    assert "for-the-platform-operator" not in [t.anchor for t in owner.toc]
    assert "for-the-platform-operator" in [t.anchor for t in admin.toc]


@pytest.mark.parametrize("slug", ["screens/engine", "does/not/exist"])
def test_a_hidden_page_is_404_like_an_unknown_one(index, slug) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(APIError) as caught:
        docs_service.page_for(index, slug, OWNER)
    assert caught.value.status_code == 404 and caught.value.code == "docs_page_not_found"


def test_a_page_of_a_hidden_topic_is_404(index) -> None:  # type: ignore[no-untyped-def]
    hidden = next(s for s in index.pages if s.startswith("settings/"))
    with pytest.raises(APIError):
        docs_service.page_for(index, hidden, OWNER)
    assert docs_service.page_for(index, hidden, ADMIN).slug == hidden


def test_page_shape_and_neighbours(index) -> None:  # type: ignore[no-untyped-def]
    tree = docs_service.tree_for(index, OWNER)
    screens = next(t for t in tree.topics if len(t.pages) >= 2)
    first, second = screens.pages[0].slug, screens.pages[1].slug
    page = docs_service.page_for(index, first, OWNER)
    assert page.prev is None and page.next is not None and page.next.slug == second
    assert docs_service.page_for(index, second, OWNER).prev.slug == first  # type: ignore[union-attr]
    assert page.version == index.version and page.title


def test_markdown_has_section_headings_for_the_toc(index) -> None:  # type: ignore[no-untyped-def]
    page = docs_service.page_for(index, "screens/widget", OWNER)
    for item in page.toc:
        assert f"## {item.title}\n" in page.markdown


def test_title_matches_put_prefixes_first(index) -> None:  # type: ignore[no-untyped-def]
    hits = docs_service.title_matches(index, "website", OWNER)
    assert hits and hits[0].title.lower().startswith("website")
    assert all(p.audience.value in OWNER for p in hits)


@pytest.mark.parametrize("role", [CobRole.OWNER, CobRole.MEMBER, CobRole.ADMIN])
def test_every_toc_anchor_is_the_element_id_of_a_section(index, role) -> None:  # type: ignore[no-untyped-def]
    """An HTML-sourced guide keeps its element ids as anchors; the dashboard uses them."""
    audiences = allowed_audiences(role)
    for slug in ("guide/flow", "guide/mcp", "guide/daily"):
        if slug not in index.pages:
            continue
        try:
            page = docs_service.page_for(index, slug, audiences)
        except APIError:
            continue
        h2 = [ln[3:] for ln in page.markdown.splitlines() if ln.startswith("## ")]
        assert [t.title for t in page.toc] == h2
        assert all(t.anchor for t in page.toc)
        section_anchors = {s.anchor for s in index.pages[slug].sections if s.anchor}
        assert {t.anchor for t in page.toc} <= section_anchors
