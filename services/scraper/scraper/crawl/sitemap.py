"""scraper/crawl/sitemap.py — Parse sitemap.xml and sitemap index files."""

import xml.etree.ElementTree as ET

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def parse_sitemap_xml(xml_content: str) -> list[str]:
    """Extract all <loc> URLs from a sitemap XML string."""
    root = ET.fromstring(xml_content)  # noqa: S314
    urls: list[str] = []
    for url_elem in root.findall(f"{{{SITEMAP_NS}}}url"):
        loc = url_elem.find(f"{{{SITEMAP_NS}}}loc")
        if loc is not None and loc.text:
            urls.append(loc.text.strip())
    return urls


def parse_sitemap_index(xml_content: str) -> list[str] | None:
    """Extract sitemap URLs from a sitemap index. Returns None if not an index."""
    root = ET.fromstring(xml_content)  # noqa: S314
    if "sitemapindex" not in root.tag:
        return None
    sitemap_urls: list[str] = []
    for sitemap_elem in root.findall(f"{{{SITEMAP_NS}}}sitemap"):
        loc = sitemap_elem.find(f"{{{SITEMAP_NS}}}loc")
        if loc is not None and loc.text:
            sitemap_urls.append(loc.text.strip())
    return sitemap_urls
