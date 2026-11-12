"""Tests for scraper/verify/rules.py — auto-verification rules."""

from scraper.verify.rules import verify_page


def test_passes_good_content():
    """Good content with sufficient words passes verification."""
    result = verify_page(
        content="This is valid content with enough words to pass the threshold.",
        word_count=60,
        content_hash="abc123",
        existing_hashes=set(),
    )
    assert result.status == "verified"
    assert result.flag_reasons == []


def test_flags_low_word_count():
    """Content below minimum word count is flagged."""
    result = verify_page(
        content="Short content.",
        word_count=10,
        content_hash="abc123",
        existing_hashes=set(),
    )
    assert result.status == "flagged"
    assert any("Low word count" in r for r in result.flag_reasons)


def test_flags_duplicate_hash():
    """Duplicate content hash is flagged."""
    result = verify_page(
        content="This is valid content with enough words to pass the threshold.",
        word_count=60,
        content_hash="abc123",
        existing_hashes={"abc123", "def456"},
    )
    assert result.status == "flagged"
    assert any("Duplicate" in r for r in result.flag_reasons)


def test_fails_empty_content():
    """Empty content fails verification."""
    result = verify_page(
        content="",
        word_count=0,
        content_hash="",
        existing_hashes=set(),
    )
    assert result.status == "failed"
    assert any("Empty" in r for r in result.flag_reasons)


def test_passes_at_exact_threshold():
    """Content at exactly the minimum word count passes."""
    result = verify_page(
        content="Word " * 50,
        word_count=50,
        content_hash="unique_hash",
        existing_hashes=set(),
    )
    assert result.status == "verified"
    assert result.flag_reasons == []
