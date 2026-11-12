"""Tests for scraper/extract/extractor.py — content extraction via Trafilatura."""

from unittest.mock import MagicMock, patch

from scraper.extract.extractor import ExtractionResult, extract_content

SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head><title>Test Page</title></head>
<body>
  <nav><a href="/">Home</a> <a href="/about">About</a></nav>
  <main>
    <h1>Main Article Title</h1>
    <p>This is the main content of the page with enough words to be meaningful.
    It contains several sentences that should be extracted by Trafilatura.
    The content extraction should strip navigation and footer elements.</p>
  </main>
  <footer><p>Copyright 2026</p></footer>
</body>
</html>
"""

EXTRACTED_CONTENT = (
    "# Main Article Title\n\n"
    "This is the main content of the page with enough words to be meaningful. "
    "It contains several sentences that should be extracted by Trafilatura. "
    "The content extraction should strip navigation and footer elements."
)


@patch("scraper.extract.extractor.trafilatura")
def test_extract_returns_result(mock_traf):
    """Extract returns ExtractionResult with content, title, word_count, hash."""
    mock_traf.extract.return_value = EXTRACTED_CONTENT
    mock_meta = MagicMock()
    mock_meta.title = "Test Page"
    mock_traf.extract_metadata.return_value = mock_meta

    result = extract_content(SAMPLE_HTML, "https://example.com/page")

    assert isinstance(result, ExtractionResult)
    assert result.content == EXTRACTED_CONTENT
    assert result.title == "Test Page"
    assert result.word_count > 0
    assert len(result.content_hash) == 64  # SHA-256 hex digest


@patch("scraper.extract.extractor.trafilatura")
def test_extract_strips_nav_footer(mock_traf):
    """Extracted content should not contain nav/footer text."""
    mock_traf.extract.return_value = EXTRACTED_CONTENT
    mock_meta = MagicMock()
    mock_meta.title = "Test Page"
    mock_traf.extract_metadata.return_value = mock_meta

    result = extract_content(SAMPLE_HTML, "https://example.com/page")

    assert "Home" not in result.content
    assert "Copyright" not in result.content
    assert "Main Article Title" in result.content


@patch("scraper.extract.extractor.trafilatura")
def test_extract_empty_page(mock_traf):
    """Empty HTML body returns empty ExtractionResult."""
    mock_traf.extract.return_value = None
    mock_traf.extract_metadata.return_value = None

    result = extract_content("<html><body></body></html>", "https://example.com/empty")

    assert result.content == ""
    assert result.word_count == 0
    assert result.content_hash == ""


def test_extract_none_input():
    """None or empty string input returns empty ExtractionResult without calling Trafilatura."""
    result = extract_content("", "https://example.com/none")

    assert result.content == ""
    assert result.title == ""
    assert result.word_count == 0
    assert result.content_hash == ""
