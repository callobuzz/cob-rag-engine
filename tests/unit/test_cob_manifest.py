"""COB's docs manifest and index: audiences, overrides, hashes, record ids."""

from pathlib import Path

import pytest

from callobuzz_rag.cob.kb import load_docs_index, parse_external_id, records_for
from callobuzz_rag.cob.manifest import ManifestError, load_manifest
from callobuzz_rag.cob.types import (
    Audience,
    CobRole,
    allowed_audiences,
)

MANIFEST_SCREENS_ONLY = """\
version: 1
topics:
  - {id: widget, title: "Website widget"}
  - {id: screens, title: "Screens"}
sources:
  - id: screens
    path: docs/cob/screens/*.md
    kind: markdown
    split: page
    slug: screens
    topic: screens
"""

SCREEN = """---
route: /widget
audience: customer
title: Website widget
summary: Keys and the embed snippet.
starters: ["How do I add the chat to my site?"]
---
Intro line.

## Embed the widget
Paste the snippet.

## Operator notes
<!-- audience: admin -->
Set RAG_WIDGET_TRUST_PROXY on the server.
"""


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def make_root(tmp_path: Path, manifest: str = MANIFEST_SCREENS_ONLY, screen: str = SCREEN) -> Path:
    write(tmp_path / "pyproject.toml", "")
    write(tmp_path / "docs/cob/screens/widget.md", screen)
    write(tmp_path / "docs/cob/manifest.yaml", manifest)
    return tmp_path


def test_markdown_page_sections_and_admin_override(tmp_path: Path) -> None:
    index = load_docs_index(make_root(tmp_path))
    page = index.pages["screens/widget"]
    assert page.route == "/widget" and page.audience == Audience.CUSTOMER
    assert [(s.anchor, s.audience) for s in page.sections] == [
        ("", Audience.CUSTOMER),
        ("embed-the-widget", Audience.CUSTOMER),
        ("operator-notes", Audience.ADMIN),
    ]
    assert "RAG_WIDGET_TRUST_PROXY" not in "".join(
        s.text for s in page.sections if s.audience != Audience.ADMIN
    )
    assert "audience:" not in "".join(s.text for s in page.sections)


def test_screen_page_without_audience_is_refused(tmp_path: Path) -> None:
    root = make_root(tmp_path, screen=SCREEN.replace("audience: customer\n", ""))
    with pytest.raises(ManifestError, match="audience"):
        load_docs_index(root)


def test_unknown_front_matter_key_is_refused(tmp_path: Path) -> None:
    root = make_root(tmp_path, screen=SCREEN.replace("route: /widget", "route: /widget\nmood: x"))
    with pytest.raises(ManifestError, match="front matter"):
        load_docs_index(root)


def test_unknown_section_override_is_refused(tmp_path: Path) -> None:
    manifest = MANIFEST_SCREENS_ONLY + "    sections:\n      sec-nope: {audience: public}\n"
    with pytest.raises(ManifestError, match="sec-nope"):
        load_docs_index(make_root(tmp_path, manifest))


def test_unknown_topic_and_missing_file_are_refused(tmp_path: Path) -> None:
    bad_topic = MANIFEST_SCREENS_ONLY.replace("topic: screens", "topic: nowhere")
    with pytest.raises(ManifestError, match="nowhere"):
        load_manifest(make_root(tmp_path, bad_topic))
    other = tmp_path / "b"
    missing = MANIFEST_SCREENS_ONLY.replace("docs/cob/screens/*.md", "docs/cob/nope/*.md")
    with pytest.raises(ManifestError, match="matches no file"):
        load_manifest(make_root(other, missing))


def test_hash_changes_with_any_source_byte(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    first = load_docs_index(root)
    again = load_docs_index(root)
    assert again is first  # cached for an unchanged tree
    write(root / "docs/cob/screens/widget.md", SCREEN.replace("Paste", "Paste it"))
    second = load_docs_index(root)
    assert second.manifest_hash != first.manifest_hash and second.version != first.version
    assert first.version.startswith("0.1.0+") and len(first.version.split("+")[1]) == 12


def test_line_endings_do_not_change_the_hash(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    first = load_docs_index(root).manifest_hash
    (root / "docs/cob/screens/widget.md").write_bytes(SCREEN.replace("\n", "\r\n").encode())
    assert load_docs_index(root).manifest_hash == first


def test_records_ids_and_metadata(tmp_path: Path) -> None:
    index = load_docs_index(make_root(tmp_path))
    recs = {r.id: r for r in records_for(index)}
    r = recs["system:cob/screens/widget#embed-the-widget"]
    assert r.metadata == {
        "audience": "customer",
        "slug": "screens/widget",
        "anchor": "embed-the-widget",
        "route": "/widget",
        "topic": "screens",
        "cob": "1",
        "cob_source": "builtin",  # COB9
    }
    assert r.title == "Website widget — Embed the widget"
    assert recs["system:cob/screens/widget#operator-notes"].metadata["audience"] == "admin"
    assert "system:cob/screens/widget#" in recs
    assert parse_external_id(r.id) == ("screens/widget", "embed-the-widget")
    assert parse_external_id("system:business-card") is None


def test_page_for_route_matches_templates_and_parents(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    write(
        root / "docs/cob/screens/setup.md",
        SCREEN.replace("/widget", "/kbs/$kbId/setup").replace("Website widget", "Setup"),
    )
    write(
        root / "docs/cob/screens/kb.md",
        SCREEN.replace("/widget", "/kbs/$kbId").replace("Website widget", "KB"),
    )
    index = load_docs_index(root)
    assert index.page_for_route("/kbs/9f3a/setup").slug == "screens/setup"  # type: ignore[union-attr]
    assert index.page_for_route("/kbs/9f3a").slug == "screens/kb"  # type: ignore[union-attr]
    assert index.page_for_route("/kbs/9f3a/setup?tab=x").slug == "screens/setup"  # type: ignore[union-attr]
    assert index.page_for_route("/kbs/9f3a/unknown/deeper").slug == "screens/kb"  # type: ignore[union-attr]
    assert index.page_for_route("/widget").slug == "screens/widget"  # type: ignore[union-attr]
    assert index.page_for_route("/nowhere") is None


def test_visible_filters_by_page_audience(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    write(
        root / "docs/cob/screens/costs.md",
        SCREEN.replace("audience: customer", "audience: admin").replace("/widget", "/costs"),
    )
    index = load_docs_index(root)
    assert {p.slug for p in index.visible(allowed_audiences(CobRole.OWNER))} == {"screens/widget"}
    assert {p.slug for p in index.visible(allowed_audiences(CobRole.ADMIN))} == {
        "screens/widget",
        "screens/costs",
    }
    assert index.visible(allowed_audiences(CobRole.PUBLIC)) == []


def test_allowed_audiences_order() -> None:
    assert allowed_audiences(CobRole.ADMIN) == ["admin", "customer", "public"]
    assert allowed_audiences(CobRole.OPERATOR_OWNER) == ["admin", "customer", "public"]
    assert allowed_audiences(CobRole.MEMBER) == ["customer", "public"]
    assert allowed_audiences(CobRole.PUBLIC) == ["public"]


def test_page_for_route_splat_matches_the_bare_prefix(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    write(
        root / "docs/cob/screens/docs.md",
        SCREEN.replace("/widget", "/docs/$").replace("Website widget", "Docs"),
    )
    index = load_docs_index(root)
    assert index.page_for_route("/docs").slug == "screens/docs"  # type: ignore[union-attr]
    assert index.page_for_route("/docs/guide/flow").slug == "screens/docs"  # type: ignore[union-attr]
    assert index.page_for_route("/docs/").slug == "screens/docs"  # type: ignore[union-attr]
