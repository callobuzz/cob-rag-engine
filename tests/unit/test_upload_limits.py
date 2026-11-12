"""Upload limits count real text, not bytes (decided 2026-10-02).

The request checks only a byte cap (`RAG_MAX_UPLOAD_BYTES`, a plan may lower it with
`max_upload_mb`); a 5 MB PDF is fine. The token cap (`RAG_MAX_DOCUMENT_TOKENS`, a plan may
lower it with `max_document_tokens`) is enforced by the pipeline AFTER parsing, on the
parsed text's real token count, and fails the job with a coded error.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.accounts import caps
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.chunking.chunker import ChunkerConfig, StructureAwareChunker
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import DocumentTooLargeError
from callobuzz_rag.parsing.models import ElementType, ParsedDocument, ParsedElement
from callobuzz_rag.pipeline.models import JobStatus
from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator

MIB = 1024 * 1024

# ----------------------------------------------------------------- settings


def test_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.max_upload_bytes == 50 * MIB
    assert s.max_document_tokens == 500_000


# ------------------------------------------------------------------ request


def _request(**settings: object) -> tuple[MagicMock, AsyncMock]:
    corpus = SimpleNamespace(id=uuid.uuid4(), qdrant_collection="c", tenant_id=uuid.uuid4())
    request = MagicMock()
    request.state.corpus = corpus
    request.state.is_admin = False
    request.state.tenant = None
    defaults = {"s3_bucket": "b", "review_expiry_days": 7, "max_upload_bytes": 50 * MIB}
    request.app.state.settings = MagicMock(**{**defaults, **settings})
    session = AsyncMock()
    session.get = AsyncMock(return_value=corpus)
    session.add = MagicMock()
    session.refresh = AsyncMock(side_effect=lambda job: setattr(job, "id", uuid.uuid4()))
    return request, session


async def _upload(request: MagicMock, session: AsyncMock, size: int, mime: str, **limits: int):
    from callobuzz_rag.api.routes.ingest import upload_document

    body = (b"%PDF-1.7\n" + b"\x00" * size)[:size] if mime == "application/pdf" else b"x" * size
    file = MagicMock(content_type=mime, filename="f.pdf")
    file.read = AsyncMock(return_value=body)
    storage = AsyncMock()
    with (
        patch.object(caps, "effective_limits", AsyncMock(return_value=caps.Limits(**limits))),
        patch.object(caps, "question_quota", AsyncMock(return_value=None)),
        patch("callobuzz_rag.api.routes.ingest.object_storage", return_value=storage),
        patch("callobuzz_rag.worker.tasks.ingest_document_task") as task,
    ):
        result = await upload_document(
            request=request,
            file=file,
            corpus_id=None,
            idempotency_key=None,
            mode="auto",
            target_document_id=None,
            auto_confirm=False,
            metadata=None,
            session=session,
        )
    return result, task


async def test_a_5_mb_pdf_is_accepted() -> None:
    """Bytes/4 used to call this ~1.3M tokens and refuse it; the text inside is tiny."""
    request, session = _request()
    result, task = await _upload(request, session, 5 * MIB, "application/pdf")
    assert result.status == "queued"
    task.delay.assert_called_once()


async def test_over_the_byte_cap_is_a_413_with_a_code() -> None:
    request, session = _request(max_upload_bytes=1 * MIB)
    with pytest.raises(APIError) as err:
        await _upload(request, session, MIB + 1, "application/pdf")
    assert err.value.status_code == 413
    assert err.value.code == "upload_too_large"
    assert err.value.extra == {"limit_bytes": MIB, "size_bytes": MIB + 1}
    session.add.assert_not_called()


async def test_a_plan_can_lower_the_byte_cap() -> None:
    request, session = _request()
    with pytest.raises(APIError) as err:
        await _upload(request, session, 3 * MIB, "application/pdf", max_upload_mb=2)
    assert err.value.status_code == 413 and err.value.code == "upload_too_large"
    assert err.value.extra["limit_bytes"] == 2 * MIB


async def test_a_plan_cannot_raise_the_byte_cap_above_the_engines() -> None:
    request, session = _request(max_upload_bytes=1 * MIB)
    with pytest.raises(APIError) as err:
        await _upload(request, session, 2 * MIB, "application/pdf", max_upload_mb=100)
    assert err.value.extra["limit_bytes"] == MIB


# ----------------------------------------------------------------- the limit


async def test_document_token_limit_is_the_lower_of_engine_and_plan() -> None:
    session = AsyncMock()
    corpus_id = uuid.uuid4()

    def limits(**kw: int) -> AsyncMock:
        return AsyncMock(return_value=caps.Limits(**kw))

    row = MagicMock()
    row.scalar_one_or_none.return_value = uuid.uuid4()
    session.execute = AsyncMock(return_value=row)
    with patch.object(caps, "effective_limits", limits(max_document_tokens=100)):
        assert await caps.document_token_limit(session, corpus_id, 500) == 100
    with patch.object(caps, "effective_limits", limits(max_document_tokens=900)):
        assert await caps.document_token_limit(session, corpus_id, 500) == 500
    with patch.object(caps, "effective_limits", limits()):
        assert await caps.document_token_limit(session, corpus_id, 500) == 500
        assert await caps.document_token_limit(session, corpus_id, None) is None


# ----------------------------------------------------------------- pipeline


def _pipeline(words: int, limit: int | None):
    session = AsyncMock()
    found = MagicMock()
    found.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=found)
    session.get = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.add_all = MagicMock()
    storage = AsyncMock()
    parser = AsyncMock()
    parser.parse = AsyncMock(
        return_value=ParsedDocument(
            elements=[ParsedElement(type=ElementType.PARAGRAPH, text="word " * words)],
            parser_name="docling",
            parser_version="x",
        )
    )
    enricher = AsyncMock()
    embedder = AsyncMock()
    store = AsyncMock()
    resolver = AsyncMock(return_value=limit)
    orch = IngestionOrchestrator(
        session=session,
        storage=storage,
        parser=parser,
        chunker=StructureAwareChunker(ChunkerConfig(min_tokens=1, overlap_tokens=0)),
        enricher=enricher,
        embedder=embedder,
        vector_store=store,
        document_token_limit=resolver,
    )
    return orch, session, storage, embedder, resolver


async def _ingest(orch: IngestionOrchestrator, job_id: uuid.UUID):
    return await orch.ingest(
        file_bytes=b"%PDF-1.7 a 5 MB scan",
        file_name="scan.pdf",
        mime_type="application/pdf",
        corpus_id=uuid.uuid4(),
        storage_key="corpus/x/raw/y",
        job_id=job_id,
    )


async def test_a_too_large_document_fails_the_job_with_a_coded_error() -> None:
    orch, session, storage, embedder, _ = _pipeline(words=2000, limit=1000)
    failed_job = SimpleNamespace(status="parsing", errors=None, finished_at=None)
    session.get = AsyncMock(side_effect=[None, failed_job])  # the API's job row, then the retry
    result = await _ingest(orch, uuid.uuid4())

    assert result.status == JobStatus.FAILED
    assert result.failed_at_step == "document_size"
    assert failed_job.status == JobStatus.FAILED
    errors = failed_job.errors
    assert errors["code"] == "document_too_large"
    assert errors["limit"] == 1000 and errors["tokens"] >= 2000
    assert "1,000" in errors["error"]
    embedder.embed_texts.assert_not_called()  # stopped before any embedding
    session.rollback.assert_awaited()
    storage.delete.assert_awaited_once_with("rag-documents", "corpus/x/raw/y")  # staged file gone


async def test_a_document_within_the_limit_goes_on() -> None:
    orch, _, storage, embedder, resolver = _pipeline(words=50, limit=1000)
    embedder.embed_texts = AsyncMock(side_effect=lambda texts, **kw: [MagicMock() for _ in texts])
    await _ingest(orch, uuid.uuid4())
    resolver.assert_awaited_once()
    storage.delete.assert_not_called()


async def test_no_limit_means_no_check() -> None:
    orch, _, storage, _, _ = _pipeline(words=2000, limit=None)
    result = await _ingest(orch, uuid.uuid4())
    assert result.failed_at_step != "document_size"
    storage.delete.assert_not_called()


def test_the_error_carries_its_details() -> None:
    err = DocumentTooLargeError(tokens=600_000, limit=500_000)
    assert err.details() == {"code": "document_too_large", "tokens": 600_000, "limit": 500_000}
    assert "600,000" in str(err) and "500,000" in str(err)
