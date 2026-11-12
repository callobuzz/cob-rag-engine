import gzip
from datetime import UTC, datetime

import pytest

from callobuzz_rag.website.sitemap import (
    SitemapTooLargeError,
    collect_sitemaps,
    parse_sitemap,
)

NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


def urlset(*items: str) -> bytes:
    return f'<?xml version="1.0"?><urlset {NS}>{"".join(items)}</urlset>'.encode()


def url(loc: str, lastmod: str = "", priority: str = "") -> str:
    lm = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
    pr = f"<priority>{priority}</priority>" if priority else ""
    return f"<url><loc>{loc}</loc>{lm}{pr}</url>"


def test_entries_with_lastmod_and_priority() -> None:
    body = urlset(
        url("https://shreedental.in/pricing", "2026-10-01", "0.8"),
        url("https://shreedental.in/contact", "2026-10-02T10:30:00+05:30", "7"),
        url("https://shreedental.in/about", "garbage", "oops"),
    )
    got = {e.url: e for e in parse_sitemap(body, scope="shreedental.in").entries}
    # a date alone is the end of that day in every time zone (UTC-12), never its midnight
    assert got["https://shreedental.in/pricing"].lastmod == datetime(2026, 10, 2, 12, tzinfo=UTC)
    assert got["https://shreedental.in/pricing"].priority == 0.8
    assert got["https://shreedental.in/contact"].lastmod == datetime(2026, 10, 2, 5, 0, tzinfo=UTC)
    assert got["https://shreedental.in/contact"].priority == 1.0  # clamped
    assert got["https://shreedental.in/about"].lastmod is None
    assert got["https://shreedental.in/about"].priority is None


def test_a_date_only_lastmod_is_the_end_of_that_day_and_a_timestamp_is_exact() -> None:
    body = urlset(
        url("https://shreedental.in/a", "2026-10-05"),
        url("https://shreedental.in/b", "2026-10-05T08:30:00Z"),
        url("https://shreedental.in/c", "2026-10-05T08:30:00"),
    )
    got = {e.url: e.lastmod for e in parse_sitemap(body, scope="shreedental.in").entries}
    assert got["https://shreedental.in/a"] == datetime(2026, 10, 6, 12, tzinfo=UTC)
    assert got["https://shreedental.in/b"] == datetime(2026, 10, 5, 8, 30, tzinfo=UTC)
    assert got["https://shreedental.in/c"] == datetime(2026, 10, 5, 8, 30, tzinfo=UTC)


def test_out_of_scope_and_excluded_locs_are_dropped() -> None:
    body = urlset(
        url("https://shreedental.in/ok"),
        url("https://evil.example/x"),
        url("https://shreedental.in/cart"),
        url("https://shreedental.in/blog/2019/a"),
    )
    doc = parse_sitemap(body, scope="shreedental.in", excludes=["/blog/*"])
    assert [e.url for e in doc.entries] == ["https://shreedental.in/ok"]


def test_gzip_and_the_size_cap_after_decompression() -> None:
    body = urlset(url("https://shreedental.in/a"))
    assert parse_sitemap(gzip.compress(body), scope="shreedental.in").entries
    bomb = gzip.compress(urlset(*[url(f"https://shreedental.in/p{i}") for i in range(5000)]))
    assert len(bomb) < 20_000
    with pytest.raises(SitemapTooLargeError):
        parse_sitemap(bomb, scope="shreedental.in", max_bytes=10_000)


def test_xml_entities_are_not_expanded() -> None:
    body = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "https://shreedental.in/leaked">]>'
        b"<urlset><url><loc>&e;</loc></url><url><loc>https://shreedental.in/ok</loc></url></urlset>"
    )
    doc = parse_sitemap(body, scope="shreedental.in")
    assert [e.url for e in doc.entries] == ["https://shreedental.in/ok"]


def test_invalid_xml_is_an_empty_document() -> None:
    assert parse_sitemap(b"<urlset><url>", scope="shreedental.in").entries == []


def _index(*locs: str) -> bytes:
    inner = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in locs)
    return f"<sitemapindex {NS}>{inner}</sitemapindex>".encode()


async def test_index_is_followed_to_depth_three_and_no_further() -> None:
    files = {
        "https://shreedental.in/s1.xml": _index("https://shreedental.in/s2.xml"),
        "https://shreedental.in/s2.xml": _index("https://shreedental.in/s3.xml"),
        "https://shreedental.in/s3.xml": urlset(url("https://shreedental.in/deep")),
    }
    fetched: list[str] = []

    async def fetch(u: str) -> bytes | None:
        fetched.append(u)
        return files.get(u)

    got = await collect_sitemaps(fetch, ["https://shreedental.in/s1.xml"], scope="shreedental.in")
    assert [e.url for e in got] == ["https://shreedental.in/deep"]

    files["https://shreedental.in/s3.xml"] = _index("https://shreedental.in/s4.xml")
    files["https://shreedental.in/s4.xml"] = urlset(url("https://shreedental.in/too-deep"))
    fetched.clear()
    got = await collect_sitemaps(fetch, ["https://shreedental.in/s1.xml"], scope="shreedental.in")
    assert got == []
    assert "https://shreedental.in/s4.xml" not in fetched
