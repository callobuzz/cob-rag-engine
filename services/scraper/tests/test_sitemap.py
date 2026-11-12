"""Tests for scraper/crawl/sitemap.py — sitemap and sitemap index parsing."""

from scraper.crawl.sitemap import parse_sitemap_index, parse_sitemap_xml

SIMPLE_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/page1</loc></url>
  <url><loc>https://example.com/page2</loc></url>
  <url><loc>https://example.com/page3</loc></url>
</urlset>
"""

EMPTY_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
</urlset>
"""

SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/sitemap1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/sitemap2.xml</loc></sitemap>
</sitemapindex>
"""


def test_parse_simple_sitemap():
    """Parse 3 URLs from a standard sitemap."""
    urls = parse_sitemap_xml(SIMPLE_SITEMAP)
    assert urls == [
        "https://example.com/page1",
        "https://example.com/page2",
        "https://example.com/page3",
    ]


def test_parse_empty_sitemap():
    """Empty sitemap returns empty list."""
    urls = parse_sitemap_xml(EMPTY_SITEMAP)
    assert urls == []


def test_parse_sitemap_index():
    """Sitemap index returns sub-sitemap URLs."""
    result = parse_sitemap_index(SITEMAP_INDEX)
    assert result == [
        "https://example.com/sitemap1.xml",
        "https://example.com/sitemap2.xml",
    ]


def test_parse_sitemap_index_returns_none_for_regular_sitemap():
    """Regular sitemap returns None from parse_sitemap_index."""
    result = parse_sitemap_index(SIMPLE_SITEMAP)
    assert result is None
