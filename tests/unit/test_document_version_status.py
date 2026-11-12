"""Tests for DocumentVersion status lifecycle."""

from callobuzz_rag.db.models.document_version import DocumentVersion


def test_document_version_has_status_column():
    """DocumentVersion should have a status column."""
    table = DocumentVersion.__table__
    assert "status" in table.columns
    col = table.columns["status"]
    assert col.type.length == 20


def test_document_version_status_accepts_active():
    """DocumentVersion status column should accept 'active' as a string value."""
    # The status column is String(20), so it accepts any string <= 20 chars.
    # This test verifies the column exists and its type allows 'active'.
    col = DocumentVersion.__table__.columns["status"]
    assert col.type.length >= len("active")


def test_document_version_status_accepts_inactive():
    """DocumentVersion status column should accept 'inactive' as a string value."""
    col = DocumentVersion.__table__.columns["status"]
    assert col.type.length >= len("inactive")
