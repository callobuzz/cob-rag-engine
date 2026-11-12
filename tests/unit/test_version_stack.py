"""Tests for version stack logic in orchestrator."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.pipeline.models import IngestMode
from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator


def _make_orchestrator(session=None, vector_store=None):
    """Helper to create orchestrator with mocked deps."""
    return IngestionOrchestrator(
        session=session or AsyncMock(),
        storage=AsyncMock(),
        parser=AsyncMock(),
        chunker=MagicMock(),
        enricher=AsyncMock(),
        embedder=AsyncMock(),
        vector_store=vector_store or AsyncMock(),
        bucket="test-bucket",
        collection="test-collection",
    )


@pytest.mark.asyncio
async def test_deactivate_version_marks_inactive():
    """Deactivating a version should set status to 'inactive'."""
    version = MagicMock()
    version.id = uuid.uuid4()
    version.status = "active"

    vector_store = AsyncMock()
    orch = _make_orchestrator(vector_store=vector_store)

    await orch._deactivate_version(version, uuid.uuid4())

    assert version.status == "inactive"


@pytest.mark.asyncio
async def test_deactivate_version_deletes_qdrant_vectors():
    """Deactivating a version should delete its vectors from Qdrant."""
    version = MagicMock()
    version.id = uuid.uuid4()
    version.status = "active"

    vector_store = AsyncMock()
    orch = _make_orchestrator(vector_store=vector_store)

    await orch._deactivate_version(version, uuid.uuid4())

    vector_store.delete_by_filter.assert_called_once_with(
        "test-collection",
        {"document_version_id": str(version.id)},
    )


@pytest.mark.asyncio
async def test_replace_mode_deactivates_old_version():
    """Replace mode should deactivate old version and create new one under same document."""
    old_version = MagicMock()
    old_version.id = uuid.uuid4()
    old_version.status = "active"

    old_doc = MagicMock()
    old_doc.id = uuid.uuid4()
    old_doc.file_name = "pricing.md"
    old_doc.versions = [old_version]

    # Mock session: first execute for fingerprint check returns None (no dup),
    # second execute for target doc returns old_doc, third re-reads it row-locked
    mock_no_match = MagicMock()
    mock_no_match.scalar_one_or_none.return_value = None
    mock_target_match = MagicMock()
    mock_target_match.scalar_one_or_none.return_value = old_doc

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[mock_no_match, mock_target_match, mock_target_match])
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.add_all = MagicMock()
    session.refresh = AsyncMock()

    parser = AsyncMock()
    parsed_doc = MagicMock()
    parsed_doc.elements = []
    parsed_doc.warnings = []
    parser.parse.return_value = parsed_doc

    chunker = MagicMock()
    chunker.chunk.return_value = []

    enricher = AsyncMock()
    enricher.enrich_batch = AsyncMock(return_value=[])

    embedder = AsyncMock()
    embedder.embed_texts = AsyncMock(return_value=[])

    vector_store = AsyncMock()
    storage = AsyncMock()

    orch = IngestionOrchestrator(
        session=session,
        storage=storage,
        parser=parser,
        chunker=chunker,
        enricher=enricher,
        embedder=embedder,
        vector_store=vector_store,
        bucket="test-bucket",
        collection="test-collection",
    )

    result = await orch.ingest(
        file_bytes=b"updated pricing content",
        file_name="pricing.md",
        mime_type="text/markdown",
        corpus_id=uuid.uuid4(),
        mode=IngestMode.REPLACE,
        target_document_id=old_doc.id,
    )

    assert old_version.status == "inactive"
    assert result.replaced_document_id == old_doc.id
