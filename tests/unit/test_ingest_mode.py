"""Tests for IngestMode enum."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.pipeline.fingerprint import find_matching_document
from callobuzz_rag.pipeline.models import IngestMode


def test_ingest_mode_auto():
    assert IngestMode.AUTO == "auto"


def test_ingest_mode_replace():
    assert IngestMode.REPLACE == "replace"


def test_ingest_mode_new():
    assert IngestMode.NEW == "new"


def test_ingest_mode_version():
    assert IngestMode.VERSION == "version"


def test_ingest_mode_default_is_auto():
    """Default mode should be auto."""
    assert IngestMode.AUTO.value == "auto"


@pytest.mark.asyncio
async def test_find_matching_document_by_filename():
    """Should find document with same filename in same corpus."""
    doc_id = uuid.uuid4()
    mock_doc = MagicMock()
    mock_doc.id = doc_id
    mock_doc.file_name = "pricing.md"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_doc

    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)

    corpus_id = uuid.uuid4()
    result = await find_matching_document(session, corpus_id, "pricing.md")
    assert result is not None
    assert result.id == doc_id


@pytest.mark.asyncio
async def test_find_matching_document_no_match():
    """Should return None when no matching filename in corpus."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None

    session = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)

    corpus_id = uuid.uuid4()
    result = await find_matching_document(session, corpus_id, "new-file.md")
    assert result is None
