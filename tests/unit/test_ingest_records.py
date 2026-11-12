"""POST /api/v1/ingest/records — text records imported without file parsing."""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from callobuzz_rag.api.schemas.ingest import IngestRecordsRequest
from callobuzz_rag.chunking.chunker import StructureAwareChunker
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.embeddings.base import EmbeddingResult
from callobuzz_rag.enrichment.models import EnrichmentResult
from callobuzz_rag.parsing.models import ElementType
from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator
from callobuzz_rag.pipeline.records import TextRecord

pytestmark = pytest.mark.usefixtures("unlimited_plan")


class TestTextRecord:
    def test_fingerprint_changes_with_any_field(self) -> None:
        base = TextRecord(id="a", text="hello", title="T", url="u", metadata={"k": "v"})
        variants = [
            TextRecord(id="a", text="hello!", title="T", url="u", metadata={"k": "v"}),
            TextRecord(id="a", text="hello", title="T2", url="u", metadata={"k": "v"}),
            TextRecord(id="a", text="hello", title="T", url="u2", metadata={"k": "v"}),
            TextRecord(id="a", text="hello", title="T", url="u", metadata={"k": "w"}),
        ]
        assert len({base.fingerprint(), *(v.fingerprint() for v in variants)}) == 5
        same = TextRecord(id="other-id", text="hello", title="T", url="u", metadata={"k": "v"})
        assert same.fingerprint() == base.fingerprint()

    def test_to_parsed_title_becomes_heading_and_paragraphs_split(self) -> None:
        parsed = TextRecord(id="a", title="Returns", text="Para one.\n\nPara two.\n\n").to_parsed()
        assert [e.type for e in parsed.elements] == [
            ElementType.HEADING,
            ElementType.PARAGRAPH,
            ElementType.PARAGRAPH,
        ]
        assert parsed.elements[0].text == "Returns"
        assert parsed.elements[0].level == 1

    def test_to_parsed_without_title(self) -> None:
        parsed = TextRecord(id="a", text="Only text").to_parsed()
        assert [e.type for e in parsed.elements] == [ElementType.PARAGRAPH]


class TestSchema:
    def test_rejects_duplicate_ids(self) -> None:
        with pytest.raises(ValidationError, match="duplicate record id"):
            IngestRecordsRequest(records=[{"id": "x", "text": "a"}, {"id": "x", "text": "b"}])

    def test_rejects_empty_and_oversized_batches(self) -> None:
        with pytest.raises(ValidationError):
            IngestRecordsRequest(records=[])
        with pytest.raises(ValidationError):
            IngestRecordsRequest(records=[{"id": str(i), "text": "t"} for i in range(1001)])

    def test_rejects_empty_text_and_id(self) -> None:
        with pytest.raises(ValidationError):
            IngestRecordsRequest(records=[{"id": "x", "text": ""}])
        with pytest.raises(ValidationError):
            IngestRecordsRequest(records=[{"id": "", "text": "a"}])


def _request(corpus: Any = None, tenant: Any = None, is_admin: bool = False) -> MagicMock:
    request = MagicMock()
    request.state.corpus = corpus
    request.state.tenant = tenant
    request.state.is_admin = is_admin
    return request


class TestRoute:
    async def test_dispatches_records_to_worker(self) -> None:
        from callobuzz_rag.api.routes.ingest import ingest_records

        corpus = MagicMock(id=uuid.uuid4(), qdrant_collection="corpus_c1", tenant_id=uuid.uuid4())
        session = AsyncMock()
        session.add = MagicMock()
        session.get = AsyncMock(return_value=corpus)
        job_id = uuid.uuid4()
        session.refresh = AsyncMock(side_effect=lambda j: setattr(j, "id", job_id))
        body = IngestRecordsRequest(
            records=[{"id": "faq-1", "title": "Returns", "text": "30 days", "metadata": {}}]
        )
        with patch("callobuzz_rag.worker.tasks.ingest_records_task") as task:
            res = await ingest_records(request=_request(corpus=corpus), body=body, session=session)

        assert res.job_id == job_id and res.records == 1 and res.status == "queued"
        job = session.add.call_args.args[0]
        assert isinstance(job, IngestJob)
        assert job.mode == "records" and job.total_documents == 1
        kwargs = task.delay.call_args.kwargs
        assert kwargs["corpus_id"] == str(corpus.id)
        assert kwargs["qdrant_collection"] == "corpus_c1"
        assert kwargs["records"][0]["id"] == "faq-1"

    async def test_tenant_cannot_import_into_foreign_corpus(self) -> None:
        from callobuzz_rag.api.routes.ingest import ingest_records

        tenant = MagicMock(id=uuid.uuid4())
        corpus = MagicMock(id=uuid.uuid4(), tenant_id=uuid.uuid4())
        session = AsyncMock()
        session.get = AsyncMock(return_value=corpus)
        body = IngestRecordsRequest(corpus_id=corpus.id, records=[{"id": "a", "text": "b"}])
        with pytest.raises(HTTPException) as exc:
            await ingest_records(request=_request(tenant=tenant), body=body, session=session)
        assert exc.value.status_code == 403

    async def test_unknown_corpus_is_404(self) -> None:
        from callobuzz_rag.api.routes.ingest import ingest_records

        session = AsyncMock()
        session.get = AsyncMock(return_value=None)
        body = IngestRecordsRequest(corpus_id=uuid.uuid4(), records=[{"id": "a", "text": "b"}])
        with pytest.raises(HTTPException) as exc:
            await ingest_records(request=_request(is_admin=True), body=body, session=session)
        assert exc.value.status_code == 404


class TestJobScoping:
    async def test_tenant_cannot_read_foreign_job(self) -> None:
        from callobuzz_rag.api.routes.ingest import get_job_status

        job = IngestJob(id=uuid.uuid4(), corpus_id=uuid.uuid4())
        session = AsyncMock()
        session.get = AsyncMock(return_value=job)
        owned = MagicMock()
        owned.scalars.return_value.all.return_value = [uuid.uuid4()]  # not the job's corpus
        session.execute = AsyncMock(return_value=owned)
        with pytest.raises(HTTPException) as exc:
            await get_job_status(
                request=_request(tenant=MagicMock(id=uuid.uuid4())),
                job_id=job.id,
                session=session,
            )
        assert exc.value.status_code == 403

    async def test_confirm_checks_tenant_before_touching_job(self) -> None:
        from datetime import UTC, datetime, timedelta

        from callobuzz_rag.api.routes.ingest import confirm_pending_review
        from callobuzz_rag.api.schemas.ingest import ConfirmReviewRequest

        job = IngestJob(
            id=uuid.uuid4(),
            corpus_id=uuid.uuid4(),
            status="pending_review",
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = job
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)
        session.get = AsyncMock(return_value=MagicMock(tenant_id=uuid.uuid4()))
        with pytest.raises(HTTPException) as exc:
            await confirm_pending_review(
                request=_request(tenant=MagicMock(id=uuid.uuid4())),
                job_id=job.id,
                body=ConfirmReviewRequest(action="new"),
                session=session,
            )
        assert exc.value.status_code == 403
        assert job.status == "pending_review"  # not flipped to expired by a stranger
        session.commit.assert_not_called()

    def test_job_exposes_record_counts(self) -> None:
        job = IngestJob(review_metadata={"records": {"created": 2, "unchanged": 1}})
        assert job.records == {"created": 2, "unchanged": 1}
        assert IngestJob(review_metadata=None).records is None


def _orchestrator(existing: list[Document]) -> tuple[IngestionOrchestrator, MagicMock, MagicMock]:
    session = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    job = IngestJob(id=uuid.uuid4(), status="queued")
    session.get = AsyncMock(return_value=job)
    rows = MagicMock()
    rows.scalars.return_value = existing
    session.execute = AsyncMock(return_value=rows)

    enricher = MagicMock()
    enricher.enrich_batch = AsyncMock(
        side_effect=lambda chunks: [EnrichmentResult() for _ in chunks]
    )
    embedder = MagicMock()
    embedder.embed_texts = AsyncMock(
        side_effect=lambda texts: [
            EmbeddingResult(vector=[0.1, 0.2], model="m", model_version="1", dimensions=2)
            for _ in texts
        ]
    )
    store = MagicMock()
    store.upsert = AsyncMock()
    store.delete_by_filter = AsyncMock()
    orch = IngestionOrchestrator(
        session=session,
        storage=MagicMock(),
        parser=MagicMock(),
        chunker=StructureAwareChunker(),
        enricher=enricher,
        embedder=embedder,
        vector_store=store,
        collection="corpus_x",
    )
    return orch, session, store


def _existing(corpus_id: uuid.UUID, record: TextRecord) -> Document:
    doc = Document(
        id=uuid.uuid4(),
        corpus_id=corpus_id,
        external_id=record.id,
        content_fingerprint=record.fingerprint(),
    )
    doc.versions = [DocumentVersion(id=uuid.uuid4(), document_id=doc.id, status="active")]
    return doc


class TestIngestRecords:
    async def test_creates_updates_and_skips(self) -> None:
        corpus_id = uuid.uuid4()
        unchanged = TextRecord(id="same", title="Same", text="Nothing changed here.")
        changed_old = TextRecord(id="changed", title="Old", text="Old text.")
        changed_new = TextRecord(id="changed", title="New", text="New text.")
        fresh = TextRecord(id="new", title="Fresh", text="Brand new.", metadata={"category": "faq"})
        doc_same = _existing(corpus_id, unchanged)
        doc_changed = _existing(corpus_id, changed_old)
        stale_version = str(doc_changed.versions[0].id)

        orch, session, store = _orchestrator([doc_same, doc_changed])
        job_id = uuid.uuid4()
        out = await orch.ingest_records([unchanged, changed_new, fresh], corpus_id, job_id)

        assert out.status == "completed", out.error
        assert (out.created, out.updated, out.unchanged) == (1, 1, 1)
        assert out.chunks_created == out.vectors_stored == 2

        points = store.upsert.call_args.args[1]
        assert {p.payload["document_id"] for p in points} == {
            str(doc_changed.id),
            next(
                str(c.args[0].id)
                for c in session.add.call_args_list
                if isinstance(c.args[0], Document)
            ),
        }
        fresh_point = next(p for p in points if p.payload.get("category") == "faq")
        assert fresh_point.sparse_vector is not None

        # The stale version is retired, and its vectors are deleted only after the upsert.
        assert doc_changed.versions[0].status == "inactive"
        store.delete_by_filter.assert_awaited_once_with(
            "corpus_x", {"document_version_id": [stale_version]}
        )
        assert doc_changed.file_name == "New"
        assert doc_changed.content_fingerprint == changed_new.fingerprint()
        new_doc = next(
            c.args[0] for c in session.add.call_args_list if isinstance(c.args[0], Document)
        )
        assert new_doc.doc_metadata == {"category": "faq"}  # kept in Postgres for rebuilds
        assert doc_changed.doc_metadata is None
        assert doc_same.versions[0].status == "active"
        session.commit.assert_awaited()
        job = await session.get(IngestJob, job_id)
        assert job.status == "completed"
        assert job.records == {"created": 1, "updated": 1, "unchanged": 1, "chunks": 2}

    async def test_all_unchanged_touches_no_vectors(self) -> None:
        corpus_id = uuid.uuid4()
        record = TextRecord(id="a", text="Same text.")
        orch, _session, store = _orchestrator([_existing(corpus_id, record)])
        out = await orch.ingest_records([record], corpus_id, uuid.uuid4())
        assert out.status == "completed" and out.unchanged == 1
        store.upsert.assert_not_called()
        store.delete_by_filter.assert_not_called()

    async def test_embedding_failure_rolls_back_and_reports_step(self) -> None:
        orch, session, store = _orchestrator([])
        orch._embedder.embed_texts = AsyncMock(side_effect=RuntimeError("ConnectError: tei"))
        out = await orch.ingest_records([TextRecord(id="a", text="x")], uuid.uuid4(), uuid.uuid4())
        assert out.status == "failed"
        assert out.failed_at_step == "embedding"
        assert "ConnectError" in (out.error or "")
        session.rollback.assert_awaited()
        store.upsert.assert_not_called()


class TestWorkerRetry:
    def test_transient_embedding_failure_is_retryable(self) -> None:
        from callobuzz_rag.worker.tasks import _is_transient_embedding_failure

        assert _is_transient_embedding_failure(
            {"status": "failed", "failed_at_step": "embedding", "error": "ConnectError: x"}
        )


class TestRetrieveRoute:
    async def test_returns_every_ranked_chunk_with_ids(self) -> None:
        """/retrieve lifts the LLM context budget and carries document/external ids."""
        from callobuzz_rag.api.routes.query import retrieve_chunks
        from callobuzz_rag.api.schemas.query import RetrieveRequest
        from callobuzz_rag.retrieval.context_assembly import CitedChunk

        doc_id = uuid.uuid4()
        cited = [
            CitedChunk(
                citation_index=1,
                chunk_id=uuid.uuid4(),
                text="Unused items can be returned within 30 days.",
                file_name="Returns",
                page=None,
                score=0.9,
                token_count=12,
                document_id=doc_id,
                external_id="faq-returns",
                prompt_text="<<<wrapped>>>",
            )
        ]
        pipeline = MagicMock(processed_query="q", score_breakdown={}, stage_timings_ms={})
        pipeline.assembled.cited_chunks = cited
        orch = MagicMock()
        orch.run_retrieval_pipeline = AsyncMock(return_value=pipeline)
        corpus = MagicMock(id=uuid.uuid4(), tenant_id=uuid.uuid4())
        request = _request(corpus=corpus)
        with (
            patch("callobuzz_rag.api.routes.query._resolve_corpus", return_value=corpus),
            patch("callobuzz_rag.api.routes.query._get_config_resolver"),
            patch("callobuzz_rag.api.routes.query._extract_llm_headers", return_value={}),
            patch("callobuzz_rag.api.routes.query._build_orchestrator", return_value=orch),
            patch("callobuzz_rag.tiers.slot.slot_spec", return_value=None),  # PIP1: no lane here
        ):
            res = await retrieve_chunks(
                request=request,
                body=RetrieveRequest(query="q", options={"final_k": 100}),
                session=AsyncMock(),
            )

        assert orch.run_retrieval_pipeline.await_args.kwargs["context_budget"] is False
        chunk = res.chunks[0]
        assert chunk.text == "Unused items can be returned within 30 days."
        assert chunk.external_id == "faq-returns"
        assert chunk.document_id == str(doc_id)


def test_retrieve_accepts_a_whole_passage_but_chat_does_not() -> None:
    from callobuzz_rag.api.schemas.query import QueryRequest, RetrieveRequest

    passage = "word " * 1200  # 6000 characters
    assert RetrieveRequest(query=passage).query == passage
    with pytest.raises(ValidationError):
        QueryRequest(query=passage)
    with pytest.raises(ValidationError):
        RetrieveRequest(query="x" * 8001)
