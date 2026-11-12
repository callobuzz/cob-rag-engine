"""scraper/extract/extractor.py — Clean content extraction via Trafilatura."""

import hashlib
from dataclasses import dataclass

import trafilatura


@dataclass
class ExtractionResult:
    """Result of content extraction from HTML."""

    content: str
    title: str
    word_count: int
    content_hash: str


def extract_content(html: str, url: str) -> ExtractionResult:
    """Extract clean content from raw HTML using Trafilatura."""
    if not html or not html.strip():
        return ExtractionResult(content="", title="", word_count=0, content_hash="")

    content = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_tables=True,
        include_links=True,
        include_images=False,
        favor_precision=True,
    )

    title = trafilatura.extract_metadata(html, default_url=url)
    title_str = title.title if title and title.title else ""

    if not content:
        return ExtractionResult(content="", title=title_str, word_count=0, content_hash="")

    word_count = len(content.split())
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    return ExtractionResult(
        content=content, title=title_str, word_count=word_count, content_hash=content_hash,
    )
