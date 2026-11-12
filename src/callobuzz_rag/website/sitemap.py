"""Sitemaps for website learning (BIZ1): XML from the internet, so parsed defensively.

lxml with entity expansion and network access off (`resolve_entities=False, no_network=True,
huge_tree=False`); gzip bodies are decompressed under the same size cap (a compression bomb is
refused); a URL outside the site's scope or excluded by the owner is dropped. A sitemap index is
followed to `MAX_INDEX_DEPTH` levels and no further.
"""

import logging
import zlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

from lxml import etree

from callobuzz_rag.website.urls import canonical, in_scope, is_excluded

logger = logging.getLogger(__name__)

MAX_INDEX_DEPTH: Final = 3
DEFAULT_MAX_BYTES: Final = 5 * 1024 * 1024


class SitemapTooLargeError(Exception):
    """A sitemap (after decompression) is bigger than the cap."""


@dataclass(frozen=True, slots=True)
class SitemapEntry:
    url: str
    lastmod: datetime | None = None
    priority: float | None = None


@dataclass(slots=True)
class SitemapDoc:
    entries: list[SitemapEntry] = field(default_factory=list)
    children: list[str] = field(default_factory=list)  # sitemap index: child sitemap URLs


def decompress(body: bytes, max_bytes: int) -> bytes:
    """Un-gzip `body` when it is gzip, refusing more than `max_bytes` of output."""
    if body[:2] != b"\x1f\x8b":
        if len(body) > max_bytes:
            raise SitemapTooLargeError("sitemap too large")
        return body
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = decoder.decompress(body, max_bytes + 1)
    except zlib.error as exc:
        raise ValueError("bad gzip") from exc
    if len(out) > max_bytes or decoder.unconsumed_tail:
        raise SitemapTooLargeError("sitemap too large")
    return out


def _lastmod(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    if "T" not in text.upper() and " " not in text:
        # A date alone ("2026-10-05"): the page may have been edited at any time that day, in
        # any time zone, so it counts as the end of that day everywhere (UTC-12), never midnight.
        return datetime(value.year, value.month, value.day, tzinfo=UTC) + timedelta(
            days=1, hours=12
        )
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _priority(text: str | None) -> float | None:
    if not text:
        return None
    try:
        value = float(text.strip())
    except ValueError:
        return None
    if value != value:  # NaN
        return None
    return min(1.0, max(0.0, value))


def parse_sitemap(
    body: bytes,
    *,
    scope: str | None = None,
    excludes: Sequence[str] = (),
    max_bytes: int = DEFAULT_MAX_BYTES,
    port: int | None = None,
) -> SitemapDoc:
    """Entries (`urlset`) or child sitemaps (`sitemapindex`) of one sitemap file.

    Invalid XML gives an empty document with a warning; a body over `max_bytes` (after
    decompression) raises SitemapTooLargeError.
    """
    data = decompress(body, max_bytes)
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, huge_tree=False, load_dtd=False
    )
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError:
        logger.warning("sitemap is not valid XML; ignored")
        return SitemapDoc()
    doc = SitemapDoc()
    kind = etree.QName(root).localname
    for node in root:
        if not isinstance(node.tag, str):
            continue
        fields = {
            etree.QName(c).localname: (c.text or "").strip() for c in node if isinstance(c.tag, str)
        }
        loc = fields.get("loc")
        if not loc:
            continue
        url = canonical(loc)
        if url is None or (scope is not None and not in_scope(loc, scope, port=port)):
            continue
        if kind == "sitemapindex":
            doc.children.append(loc)
        elif kind == "urlset":
            if is_excluded(url, excludes):
                continue
            doc.entries.append(
                SitemapEntry(
                    url, _lastmod(fields.get("lastmod")), _priority(fields.get("priority"))
                )
            )
    return doc


async def collect_sitemaps(
    fetch: Callable[[str], Awaitable[bytes | None]],
    starts: Sequence[str],
    *,
    scope: str,
    excludes: Sequence[str] = (),
    max_urls: int = 20_000,
    max_bytes: int = DEFAULT_MAX_BYTES,
    port: int | None = None,
) -> list[SitemapEntry]:
    """Read `starts` and the indexes they point to (depth <= MAX_INDEX_DEPTH); first entry wins."""
    seen_files: set[str] = set()
    entries: dict[str, SitemapEntry] = {}
    level = list(dict.fromkeys(starts))
    for _depth in range(MAX_INDEX_DEPTH):
        nxt: list[str] = []
        for url in level:
            if url in seen_files or len(entries) >= max_urls:
                continue
            seen_files.add(url)
            body = await fetch(url)
            if body is None:
                continue
            try:
                doc = parse_sitemap(
                    body, scope=scope, excludes=excludes, max_bytes=max_bytes, port=port
                )
            except (SitemapTooLargeError, ValueError):
                logger.warning("sitemap refused (too large or not gzip)")
                continue
            for entry in doc.entries:
                if len(entries) < max_urls:
                    entries.setdefault(entry.url, entry)
            nxt.extend(doc.children)
        level = nxt
        if not level:
            break
    return list(entries.values())
