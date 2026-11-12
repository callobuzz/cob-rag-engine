"""Tests for document management endpoints."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from callobuzz_rag.api.routes.documents import delete_document
from callobuzz_rag.api.schemas.documents import (
    DocumentListResponse,
    DocumentSummary,
    MergeResponse,
    RestoreResponse,
    VersionListResponse,
    VersionSummary,
)


def _make_request(corpus, vector_store) -> MagicMock:
    request = MagicMock()
    request.state.corpus = corpus
    request.app.state.vector_store = vector_store
    return request


@pytest.mark.asyncio
async def test_delete_document_purges_vectors_and_cascades():
    """Delete purges Qdrant vectors by document_id, then deletes the row."""
    corpus = SimpleNamespace(id=uuid.uuid4(), qdrant_collection="corpus_test")
    document_id = uuid.uuid4()
    doc = SimpleNamespace(id=document_id, external_id=None, corpus_id=corpus.id, versions=[])

    vector_store = MagicMock()
    vector_store.delete_by_filter = AsyncMock()

    session = MagicMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = doc
    session.execute = AsyncMock(return_value=exec_result)
    session.delete = AsyncMock()
    session.commit = AsyncMock()

    request = _make_request(corpus, vector_store)

    result = await delete_document(request, document_id, session, corpus)

    assert result is None
    vector_store.delete_by_filter.assert_awaited_once_with(
        "corpus_test", {"document_id": str(document_id)}
    )
    session.delete.assert_awaited_once_with(doc)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_document_not_found_raises_404():
    corpus = SimpleNamespace(id=uuid.uuid4(), qdrant_collection="corpus_test")
    vector_store = MagicMock()
    vector_store.delete_by_filter = AsyncMock()

    session = MagicMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=exec_result)
    session.delete = AsyncMock()
    session.commit = AsyncMock()

    request = _make_request(corpus, vector_store)

    with pytest.raises(HTTPException) as exc:
        await delete_document(request, uuid.uuid4(), session, corpus)

    assert exc.value.status_code == 404
    vector_store.delete_by_filter.assert_not_awaited()
    session.delete.assert_not_awaited()


def test_document_summary_schema():
    summary = DocumentSummary(
        id=uuid.uuid4(),
        file_name="pricing.md",
        mime_type="text/markdown",
        size_bytes=1024,
        ingested_at="2026-03-09T00:00:00Z",
        active_version_id=uuid.uuid4(),
        version_count=3,
    )
    assert summary.file_name == "pricing.md"
    assert summary.version_count == 3


def test_version_summary_schema():
    summary = VersionSummary(
        id=uuid.uuid4(),
        status="active",
        chunk_count=15,
        parser_version="1.15.2",
        chunk_schema_version="1.0",
        created_at="2026-03-09T00:00:00Z",
    )
    assert summary.status == "active"
    assert summary.chunk_count == 15


def test_merge_response_schema():
    resp = MergeResponse(
        old_document_id=uuid.uuid4(),
        new_document_id=uuid.uuid4(),
        versions_preserved=2,
    )
    assert resp.status == "merged"


def test_restore_response_schema():
    resp = RestoreResponse(
        document_id=uuid.uuid4(),
        restored_version_id=uuid.uuid4(),
        previous_active_version_id=uuid.uuid4(),
    )
    assert resp.status == "restored"


def test_document_list_response():
    items = [
        DocumentSummary(
            id=uuid.uuid4(),
            file_name="doc1.pdf",
            mime_type="application/pdf",
            size_bytes=2048,
            ingested_at="2026-03-09T00:00:00Z",
            version_count=1,
        )
    ]
    resp = DocumentListResponse(items=items, count=1)
    assert resp.count == 1
    assert len(resp.items) == 1


def test_version_list_response():
    doc_id = uuid.uuid4()
    versions = [
        VersionSummary(
            id=uuid.uuid4(),
            status="active",
            chunk_count=10,
            parser_version="1.0",
            chunk_schema_version="1.0",
            created_at="2026-03-09T00:00:00Z",
        )
    ]
    resp = VersionListResponse(
        document_id=doc_id,
        file_name="test.md",
        versions=versions,
        count=1,
    )
    assert resp.document_id == doc_id
    assert resp.count == 1
