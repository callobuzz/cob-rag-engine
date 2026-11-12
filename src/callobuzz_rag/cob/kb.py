"""COB's documentation as data: the docs index, and the text records the sync ingests.

`load_docs_index()` reads the manifest and every source it lists (cached per manifest hash, so a
changed file builds a new index and an unchanged tree is read once). `records_for(index)` turns
every section into one `TextRecord` with the id `system:cob/<slug>#<anchor>`; the audience travels
in the record's metadata and ends up in the Qdrant payload (`meta_audience`), which is what
retrieval filters on.
"""

from functools import lru_cache
from pathlib import Path

from callobuzz_rag import __version__
from callobuzz_rag.cob.manifest import (
    ManifestError,
    ManifestSource,
    docs_root,
    load_manifest,
    manifest_hash,
    source_files,
)
from callobuzz_rag.cob.sources.html import html_pages
from callobuzz_rag.cob.sources.markdown import markdown_pages
from callobuzz_rag.cob.types import DocPage, DocsIndex
from callobuzz_rag.pipeline.records import SYSTEM_RECORD_PREFIX, TextRecord

RECORD_PREFIX = f"{SYSTEM_RECORD_PREFIX}cob/"


def external_id(slug: str, anchor: str) -> str:
    return f"{RECORD_PREFIX}{slug}#{anchor}"


def parse_external_id(value: str) -> tuple[str, str] | None:
    """(slug, anchor) of a COB record id, None for any other id."""
    if not value.startswith(RECORD_PREFIX) or "#" not in value:
        return None
    slug, anchor = value[len(RECORD_PREFIX) :].split("#", 1)
    return slug, anchor


def _pages_of(root: Path, source: ManifestSource, seen: set[str]) -> list[DocPage]:
    pages: list[DocPage] = []
    for file in source_files(root, source):
        text = file.read_text(encoding="utf-8")
        where = file.relative_to(root).as_posix()
        if source.kind == "html":
            pages.extend(html_pages(text, slug=source.slug, source=source, seen_keys=seen))
            continue
        if source.is_glob:
            if source.split != "page":
                raise ManifestError(f"Source '{source.id}': a glob needs split: page.")
            slug = f"{source.slug}/{file.stem}"
        else:
            slug = source.slug
        pages.extend(
            markdown_pages(
                text,
                slug=slug,
                source=source,
                split=source.split,
                where=where,
                seen_keys=seen,
            )
        )
    return pages


@lru_cache(maxsize=4)
def _build(root_str: str, digest: str) -> DocsIndex:
    root = Path(root_str)
    manifest = load_manifest(root)
    topics = manifest.topic_ids()
    pages: dict[str, DocPage] = {}
    for source in manifest.sources:
        seen: set[str] = set()
        for page in _pages_of(root, source, seen):
            if page.topic not in topics:
                raise ManifestError(f"Page '{page.slug}' names the unknown topic '{page.topic}'.")
            if page.slug in pages:
                raise ManifestError(f"Two pages share the slug '{page.slug}'.")
            pages[page.slug] = page
        unknown = sorted(set(source.sections) - seen)
        if unknown:
            raise ManifestError(
                f"Source '{source.id}': section override(s) {unknown} name no existing section."
            )
    return DocsIndex(version=f"{__version__}+{digest[:12]}", manifest_hash=digest, pages=pages)


def load_docs_index(root: Path | None = None) -> DocsIndex:
    root = (root or docs_root()).resolve()
    manifest = load_manifest(root)
    return _build(str(root), manifest_hash(root, manifest))


def records_for(index: DocsIndex) -> list[TextRecord]:
    """One record per non-empty section, in page order."""
    records: list[TextRecord] = []
    for page in index.pages.values():
        for section in page.sections:
            title = page.title if not section.anchor else f"{page.title} — {section.title}"
            records.append(
                TextRecord(
                    id=external_id(page.slug, section.anchor),
                    title=title,
                    text=f"{page.title}\n\n{section.text}",
                    url=page.public_url,
                    metadata={
                        "audience": section.audience.value,
                        "slug": page.slug,
                        "anchor": section.anchor,
                        "route": page.route or "",
                        "topic": page.topic,
                        "cob": "1",
                        "cob_source": "builtin",
                    },
                )
            )
    return records
