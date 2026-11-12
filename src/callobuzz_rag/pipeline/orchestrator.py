"""Ingestion pipeline orchestrator.

Wires all providers together into a complete ingestion flow.
All providers are injected via constructor (dependency injection).

MVP Limitation: The entire pipeline runs in a single DB transaction.
If a late step fails, all records are rolled back. Phase 2 should add
per-step checkpointing for large documents.
"""

import io
import logging
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.migration.context import DualWriteTarget
    from callobuzz_rag.parsing.models import ParsedDocument
    from callobuzz_rag.pipeline.records import RecordsResult, TextRecord
    from callobuzz_rag.transcription.base import TranscriptionProvider, TranscriptionResult

from callobuzz_rag.chunking.chunker import StructureAwareChunker
from callobuzz_rag.chunking.index_text import index_text
from callobuzz_rag.chunking.models import ChunkResult
from callobuzz_rag.core.exceptions import (
    DocumentLimitReachedError,
    DocumentTooLargeError,
    NoTextFoundError,
)
from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.db.models.chunk_metadata import ChunkMetadata as ChunkMetadataModel
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.db.models.embedding import Embedding as EmbeddingModel
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.embeddings.base import EmbeddingProvider
from callobuzz_rag.enrichment.base import EnrichmentProvider
from callobuzz_rag.enrichment.models import EnrichmentResult
from callobuzz_rag.parsing.base import DocumentParserProvider
from callobuzz_rag.parsing.errors import ParsingError
from callobuzz_rag.pipeline.boilerplate import (
    BoilerplateOutcome,
    SqlBlockHashStore,
    apply_boilerplate,
    boilerplate_enabled,
    is_shared_chunk,
)
from callobuzz_rag.pipeline.fingerprint import compute_fingerprint, find_matching_document
from callobuzz_rag.pipeline.models import IngestMode, JobStatus, PipelineResult
from callobuzz_rag.pipeline.similarity import find_similar_documents, spread_sample
from callobuzz_rag.pipeline.text_hash import text_hash
from callobuzz_rag.retrieval.sparse import text_to_sparse_vector
from callobuzz_rag.storage.base import ObjectStorageProvider
from callobuzz_rag.storage.key_builder import build_raw_document_key
from callobuzz_rag.vectordb.base import (
    ChunkPayload,
    SparseVector,
    VectorPoint,
    VectorStorageProvider,
)

logger = logging.getLogger(__name__)

# Points per Qdrant request; keeps one request well under Qdrant's 32MB body limit.
_UPSERT_BATCH = 256

# (session, corpus_id, job_id, new documents about to be created) -> None, raising
# DocumentLimitReachedError when the tenant's limit has no room (accounts A6 backstop;
# the worker passes `accounts.caps.ensure_document_room_for_job`).
DocumentRoomCheck = Callable[[AsyncSession, uuid.UUID, uuid.UUID, int], Awaitable[None]]

_DOCUMENT_LIMIT_STEP = "document_limit"
_DOCUMENT_SIZE_STEP = "document_size"
_NO_TEXT_STEP = "no_text"

# (session, corpus_id) -> the most tokens of parsed text one document may hold (None: no
# limit). The worker passes the engine limit lowered by the tenant's plan.
DocumentTokenLimit = Callable[[AsyncSession, uuid.UUID], Awaitable[int | None]]


def _points(
    chunk_models: list[Chunk],
    vectors: list[list[float]],
    payloads: list[dict[str, Any]],
    sparse_vectors: list[SparseVector | None],
) -> list[VectorPoint]:
    return [
        VectorPoint(id=str(m.id), vector=v, payload=p, sparse_vector=s)
        for m, v, p, s in zip(chunk_models, vectors, payloads, sparse_vectors, strict=True)
    ]


class IngestionOrchestrator:
    """Orchestrates the complete document ingestion pipeline."""

    def __init__(
        self,
        session: AsyncSession,
        storage: ObjectStorageProvider,
        parser: DocumentParserProvider,
        chunker: StructureAwareChunker,
        enricher: EnrichmentProvider,
        embedder: EmbeddingProvider,
        vector_store: VectorStorageProvider,
        bucket: str = "rag-documents",
        collection: str = "chunks",
        transcriber: "TranscriptionProvider | None" = None,
        settings: "Settings | None" = None,
        document_room: DocumentRoomCheck | None = None,
        document_token_limit: DocumentTokenLimit | None = None,
    ) -> None:
        self._document_token_limit = document_token_limit
        self._session = session
        self._document_room = document_room
        self._storage = storage
        self._parser = parser
        self._chunker = chunker
        self._enricher = enricher
        self._embedder = embedder
        self._vector_store = vector_store
        self._bucket = bucket
        self._collection = collection
        self._transcriber = transcriber
        self._settings = settings

    async def ingest(
        self,
        file_bytes: bytes,
        file_name: str,
        mime_type: str,
        corpus_id: uuid.UUID,
        storage_key: str | None = None,
        force: bool = False,
        mode: IngestMode = IngestMode.AUTO,
        target_document_id: uuid.UUID | None = None,
        auto_confirm: bool = False,
        job_id: uuid.UUID | None = None,
        doc_metadata: dict[str, str] | None = None,
    ) -> PipelineResult:
        """Run the full ingestion pipeline for a single document."""
        if job_id is None:
            job_id = uuid.uuid4()
        document_id = uuid.uuid4()
        version_id = uuid.uuid4()
        result = PipelineResult(job_id=job_id, status=JobStatus.QUEUED, document_id=document_id)

        job: IngestJob | None = None
        t_total_start = time.monotonic()
        stale_versions: list[str] = []

        try:
            # 1. Fingerprint & dedup check
            fingerprint = compute_fingerprint(file_bytes)

            # Mode: NEW — skip all matching, always create fresh document
            if mode == IngestMode.NEW:
                pass  # Fall through to document creation
            else:
                # Fingerprint dedup (applies to auto, replace, version modes)
                existing_doc = await self._session.execute(
                    select(Document).where(
                        Document.content_fingerprint == fingerprint,
                        Document.corpus_id == corpus_id,
                    )
                )
                existing = existing_doc.scalar_one_or_none()
                if existing and not force:
                    logger.info(
                        "Duplicate detected: %s (fingerprint=%s...)",
                        file_name,
                        fingerprint[:12],
                    )
                    result.status = JobStatus.COMPLETED
                    result.document_id = existing.id
                    result.warnings.append("Duplicate file — already ingested (identical content)")
                    await self._settle_job_early(job_id, JobStatus.COMPLETED, processed=1)
                    return result

            # Determine target document for replace/version/auto modes
            target_doc: Document | None = None

            if mode in (IngestMode.REPLACE, IngestMode.VERSION):
                if target_document_id:
                    from sqlalchemy.orm import selectinload

                    target_result = await self._session.execute(
                        select(Document)
                        .where(Document.id == target_document_id)
                        .options(selectinload(Document.versions))
                    )
                    target_doc = target_result.scalar_one_or_none()
                    if not target_doc:
                        result.status = JobStatus.FAILED
                        result.error = f"Target document {target_document_id} not found"
                        await self._settle_job_early(job_id, JobStatus.FAILED, error=result.error)
                        return result
                else:
                    target_doc = await find_matching_document(self._session, corpus_id, file_name)
            elif mode == IngestMode.AUTO:
                target_doc = await find_matching_document(self._session, corpus_id, file_name)

            # Validate: REPLACE mode requires a target document
            if mode == IngestMode.REPLACE and target_doc is None:
                result.status = JobStatus.FAILED
                result.error = "No target document found for replace mode"
                await self._settle_job_early(job_id, JobStatus.FAILED, error=result.error)
                return result

            # 2. Create or reuse Document + Version
            try:
                from importlib.metadata import version as pkg_version

                docling_ver = pkg_version("docling")
            except Exception:
                docling_ver = "unknown"

            # Look up existing job (created by API) or create a new one
            existing_job = await self._session.get(IngestJob, job_id)
            if existing_job:
                job = existing_job
            else:
                job = IngestJob(id=job_id, status=JobStatus.QUEUED, total_documents=1)
                self._session.add(job)

            if target_doc:
                # Version stack: reuse existing Document. Locked, and its versions
                # re-read, before one is retired: a concurrent ingest, restore or review
                # finalize of this document waits for this one to commit, so exactly one
                # version ends up active (a partial unique index enforces it).
                doc = await self._lock_document(target_doc.id)
                doc.content_fingerprint = fingerprint
                doc.size_bytes = len(file_bytes)
                doc.mime_type = mime_type
                doc.doc_metadata = dict(doc_metadata) if doc_metadata else None

                # Retire the active versions. Their vectors stay in the vector store until
                # the new version's are in (below): a job that fails first (a scan with no
                # text, an embedding outage) rolls the retirement back, and the document
                # must still be searchable then.
                for v in doc.versions:
                    if v.status == "active":
                        v.status = "inactive"
                        stale_versions.append(str(v.id))

                result.replaced_document_id = doc.id
                document_id = doc.id
            else:
                # New document: the tenant's document limit is re-checked first (A6).
                await self._ensure_document_room(corpus_id, job_id, 1)
                doc = Document(
                    id=document_id,
                    corpus_id=corpus_id,
                    file_name=file_name,
                    mime_type=mime_type,
                    size_bytes=len(file_bytes),
                    content_fingerprint=fingerprint,
                    doc_metadata=dict(doc_metadata) if doc_metadata else None,
                )
                self._session.add(doc)

            result.document_id = doc.id
            result.version_number = len(doc.versions) + 1 if target_doc else 1

            version = DocumentVersion(
                id=version_id,
                document_id=doc.id,
                parser_version=docling_ver,
                chunk_schema_version="1.0",
                status="processing",
            )
            self._session.add(version)
            await self._session.flush()

            # 3. Store raw file
            await self._update_job_status(job, JobStatus.UPLOADING)
            if storage_key is None:
                storage_key = build_raw_document_key(corpus_id, document_id)
                await self._storage.upload(
                    self._bucket,
                    storage_key,
                    io.BytesIO(file_bytes),
                    content_type=mime_type,
                )
            doc.raw_storage_key = storage_key

            # 4. Parse document (or transcribe audio)
            await self._update_job_status(job, JobStatus.PARSING)
            t0 = time.monotonic()
            strip_boilerplate, parse_options = await self._boilerplate_scope(corpus_id)

            from callobuzz_rag.transcription.base import AUDIO_MIME_TYPES

            if mime_type in AUDIO_MIME_TYPES:
                if self._transcriber is None:
                    from callobuzz_rag.core.exceptions import TranscriptionError

                    raise TranscriptionError(
                        f"Audio file {file_name} requires transcription but no "
                        f"provider configured. "
                        f"Set RAG_TRANSCRIPTION_PROVIDER to enable audio support."
                    )
                transcript = await self._transcriber.transcribe(file_bytes, file_name)
                parsed = self._transcript_to_parsed(transcript, file_name, mime_type)
            else:
                with tempfile.NamedTemporaryFile(
                    suffix=Path(file_name).suffix, delete=False
                ) as tmp:
                    tmp.write(file_bytes)
                    tmp_path = Path(tmp.name)
                try:
                    parsed = await self._parser.parse(tmp_path, mime_type, **parse_options)
                finally:
                    tmp_path.unlink(missing_ok=True)
            result.parse_duration_ms = int((time.monotonic() - t0) * 1000)

            await self._ensure_document_size(corpus_id, parsed)
            result.warnings.extend(parsed.warnings)

            # 4b. Page furniture repeated across the knowledge base's documents
            boilerplate = BoilerplateOutcome()
            if strip_boilerplate:
                parsed, boilerplate = await self._strip_boilerplate(corpus_id, doc.id, parsed)
                if boilerplate.warning:
                    result.warnings.append(boilerplate.warning)

            # 5. Chunk
            await self._update_job_status(job, JobStatus.CHUNKING)
            t0 = time.monotonic()
            chunks = self._chunker.chunk(parsed)
            if not chunks:
                # Nothing to index: a scan with OCR off, an empty file. Qdrant refuses an
                # empty write, so stop here with a message the owner can act on.
                raise NoTextFoundError()
            chunk_models = self._build_chunk_models(chunks, version_id, corpus_id)
            self._session.add_all(chunk_models)
            await self._session.flush()
            result.chunks_created = len(chunk_models)
            result.chunk_duration_ms = int((time.monotonic() - t0) * 1000)

            # Auto mode: if no filename match, check content similarity
            if mode == IngestMode.AUTO and target_doc is None and chunk_models:
                # A spread over the whole document (first, middle, last), not its first
                # five chunks: the top of a page is where its furniture sits.
                sampled = self._content_sample(chunks, boilerplate)
                chunk_texts = [index_text(c.text, c.structure_path) for c in sampled]
                similar_docs = await find_similar_documents(
                    chunks_text=chunk_texts,
                    corpus_id=corpus_id,
                    embedder=self._embedder,
                    vector_store=self._vector_store,
                    collection=self._collection,
                    min_score=self._similarity_floor(),
                    ignore_text_hashes=boilerplate.dropped_hashes,
                )
                if similar_docs:
                    result.similar_documents = similar_docs

                # Auto mode: if similar docs found and not auto_confirm, pause for review
                if similar_docs and not auto_confirm:
                    result.status = JobStatus.PENDING_REVIEW
                    result.warnings.append(
                        f"Found {len(similar_docs)} similar document(s). "
                        "Confirm via POST /api/v1/ingest/{job_id}/confirm"
                    )
                    # Rollback flushed-but-uncommitted chunks/version/job records.
                    # They will be re-created when the job is confirmed and re-run.
                    await self._session.rollback()
                    return result

            # 6. Enrich (non-fatal — continue with empty metadata on failure)
            await self._update_job_status(job, JobStatus.ENRICHING)
            t0 = time.monotonic()
            try:
                enrichments = await self._enricher.enrich_batch(chunks)
            except Exception as e:
                logger.warning("Enrichment failed for %s, using defaults: %s", file_name, e)
                enrichments = [EnrichmentResult() for _ in chunks]
                result.warnings.append(f"Enrichment degraded: {e}")
            result.enrich_duration_ms = int((time.monotonic() - t0) * 1000)

            meta_models: list[ChunkMetadataModel] = []
            for chunk_model, chunk_result, enrichment in zip(
                chunk_models, chunks, enrichments, strict=True
            ):
                meta = ChunkMetadataModel(
                    chunk_id=chunk_model.id,
                    summary=enrichment.summary,
                    keywords=enrichment.keywords,
                    confidence_score=enrichment.confidence_score,
                    table_json=chunk_result.table_json,
                )
                meta_models.append(meta)
                chunk_model.language = enrichment.language

            self._session.add_all(meta_models)

            # 7. Embed
            await self._update_job_status(job, JobStatus.EMBEDDING)
            t0 = time.monotonic()
            texts = [index_text(c.text, c.structure_path) for c in chunks]
            embeddings = await self._embedder.embed_texts(texts)
            emb_models: list[EmbeddingModel] = []
            for chunk_model, emb in zip(chunk_models, embeddings, strict=True):
                emb_record = EmbeddingModel(
                    chunk_id=chunk_model.id,
                    model=emb.model,
                    model_version=emb.model_version,
                    vector_ref=str(chunk_model.id),
                    vector_type="text",
                    dimensions=emb.dimensions,
                )
                emb_models.append(emb_record)
            self._session.add_all(emb_models)
            result.embed_duration_ms = int((time.monotonic() - t0) * 1000)

            # 7b. Dual-write check (Phase 8: embedding migration)
            dual_target = await self._get_dual_write_target(corpus_id)

            # 8. Store vectors in Qdrant
            await self._update_job_status(job, JobStatus.STORING)
            t0 = time.monotonic()

            # Extract well-known metadata fields (copy to avoid mutating caller's dict)
            _meta = dict(doc_metadata or {})
            doc_category = _meta.pop("category", None)
            doc_user_id = _meta.pop("user_id", None)

            payloads: list[dict[str, Any]] = []
            sparse_vectors: list[SparseVector | None] = []
            for chunk_model, chunk_result, enrichment in zip(
                chunk_models, chunks, enrichments, strict=True
            ):
                payloads.append(
                    ChunkPayload(
                        chunk_id=str(chunk_model.id),
                        corpus_id=str(corpus_id),
                        document_id=str(doc.id),
                        document_version_id=str(version_id),
                        page=chunk_result.page,
                        is_table=chunk_result.is_table,
                        is_code=chunk_result.is_code,
                        language=enrichment.language,
                        keywords=enrichment.keywords or [],
                        category=doc_category,
                        user_id=doc_user_id,
                        metadata=_meta,
                        text_hash=chunk_model.text_hash,
                    ).to_dict()
                )
                sparse_data = text_to_sparse_vector(
                    index_text(chunk_result.text, chunk_result.structure_path)
                )
                sparse_vectors.append(
                    sparse_data.to_sparse_vector() if not sparse_data.is_empty() else None
                )
            points = _points(chunk_models, [e.vector for e in embeddings], payloads, sparse_vectors)
            await self._vector_store.upsert(self._collection, points)
            result.vectors_stored = len(points)

            # 8b. The same points, in the target model's vectors, into a running
            # embedding migration's new collection (best-effort)
            if dual_target is not None:
                await self._dual_write(dual_target, texts, chunk_models, payloads, sparse_vectors)

            result.store_duration_ms = int((time.monotonic() - t0) * 1000)

            # 9. Complete
            from datetime import UTC, datetime

            version.status = "active"
            await self._update_job_status(job, JobStatus.COMPLETED)
            job.processed_documents = 1
            job.finished_at = datetime.now(UTC)
            if boilerplate.dropped:
                job.review_metadata = {
                    **(job.review_metadata or {}),
                    "boilerplate_stripped": boilerplate.dropped,
                }
            await self._session.commit()

            # The retired versions' vectors go only after the commit that makes the new
            # version active (the order corpus deletion uses): a failed delete leaves
            # stale duplicates, never a rolled-back job with orphaned new points, and a
            # failed commit never leaves the old version active without its vectors.
            for stale_id in stale_versions:
                try:
                    await self._drop_version_vectors(stale_id, corpus_id)
                except Exception:
                    logger.error(
                        "Retired version %s vectors left in %s; re-run reindex or cleanup",
                        stale_id,
                        self._collection,
                        exc_info=True,
                    )

            result.status = JobStatus.COMPLETED
            result.total_duration_ms = int((time.monotonic() - t_total_start) * 1000)

            logger.info(
                "Ingestion complete: job_id=%s file=%s chunks=%d vectors=%d "
                "parse_ms=%d chunk_ms=%d enrich_ms=%d embed_ms=%d store_ms=%d total_ms=%d",
                str(job_id),
                file_name,
                result.chunks_created,
                result.vectors_stored,
                result.parse_duration_ms,
                result.chunk_duration_ms,
                result.enrich_duration_ms,
                result.embed_duration_ms,
                result.store_duration_ms,
                result.total_duration_ms,
            )

            return result

        except ParsingError as e:
            logger.error("Pipeline PARSING failed for %s: %s", file_name, e, exc_info=True)
            result.failed_at_step = "parsing"
            return await self._handle_failure(job_id, result, e)

        except DocumentLimitReachedError as e:
            logger.warning("Ingest job %s refused for %s: %s", job_id, file_name, e)
            result.failed_at_step = _DOCUMENT_LIMIT_STEP
            return await self._handle_failure(job_id, result, e)

        except DocumentTooLargeError as e:
            logger.warning("Ingest job %s refused for %s: %s", job_id, file_name, e)
            result.failed_at_step = _DOCUMENT_SIZE_STEP
            failed = await self._handle_failure(job_id, result, e)
            # The file the API staged is of no use any more: no row points at it.
            if storage_key is not None:
                from callobuzz_rag.storage.cleanup import discard_object

                await discard_object(
                    self._storage, self._bucket, storage_key, reason="document too large"
                )
            return failed

        except NoTextFoundError as e:
            logger.warning("Ingest job %s found no readable text in %s", job_id, file_name)
            result.failed_at_step = _NO_TEXT_STEP
            failed = await self._handle_failure(job_id, result, e)
            # No row points at the staged file any more: it would sit in storage forever.
            if storage_key is not None:
                from callobuzz_rag.storage.cleanup import discard_object

                await discard_object(
                    self._storage, self._bucket, storage_key, reason="no readable text"
                )
            return failed

        except Exception as e:
            current_step = job.status if job is not None else "unknown"
            logger.error(
                "Pipeline failed for %s at %s: %s", file_name, current_step, e, exc_info=True
            )
            result.failed_at_step = str(current_step)
            return await self._handle_failure(job_id, result, e)

    async def ingest_records(
        self,
        records: "list[TextRecord]",
        corpus_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> "RecordsResult":
        """Import text records: chunk, enrich, embed and store, with no parsing or upload.

        One Document per record, keyed by (corpus_id, external_id). A record whose
        content fingerprint is unchanged is skipped; a changed one gets a new version
        and its old vectors are removed. The whole batch commits or rolls back together.
        """
        from datetime import UTC, datetime

        from sqlalchemy.orm import selectinload

        from callobuzz_rag.pipeline.records import (
            RECORD_MIME_TYPE,
            RECORD_PARSER_VERSION,
            RecordsResult,
            is_system_record_id,
        )

        outcome = RecordsResult()
        t_start = time.monotonic()
        job: IngestJob | None = None
        try:
            job = await self._session.get(IngestJob, job_id)
            if job is None:
                job = IngestJob(id=job_id, corpus_id=corpus_id, total_documents=len(records))
                self._session.add(job)
            await self._update_job_status(job, JobStatus.CHUNKING)

            # Locked in id order (no deadlock between overlapping imports) and re-read,
            # so a concurrent change to one of these documents cannot leave two active
            # versions.
            existing_rows = await self._session.execute(
                select(Document)
                .where(
                    Document.corpus_id == corpus_id,
                    Document.external_id.in_([r.id for r in records]),
                )
                .order_by(Document.id)
                .with_for_update()
                .options(selectinload(Document.versions))
                .execution_options(populate_existing=True)
            )
            existing = {d.external_id: d for d in existing_rows.scalars()}
            # Before anything changes: the tenant's document limit must have room for
            # the records that are not yet documents (A6). The batch is all or nothing.
            # System documents (the business card) use no allowance.
            await self._ensure_document_room(
                corpus_id,
                job_id,
                len({r.id for r in records if not is_system_record_id(r.id)} - existing.keys()),
            )

            strip_boilerplate, _ = await self._boilerplate_scope(corpus_id)
            stripped_blocks = 0
            stale_versions: list[str] = []
            all_chunks: list[ChunkResult] = []
            all_models: list[Chunk] = []
            owners: list[tuple[Document, uuid.UUID, dict[str, str]]] = []
            new_versions: list[DocumentVersion] = []

            for record in records:
                fingerprint = record.fingerprint()
                doc = existing.get(record.id)
                if doc is not None and doc.content_fingerprint == fingerprint:
                    outcome.unchanged += 1
                    continue
                if doc is None:
                    doc = Document(
                        id=uuid.uuid4(),
                        corpus_id=corpus_id,
                        external_id=record.id,
                        mime_type=RECORD_MIME_TYPE,
                    )
                    self._session.add(doc)
                    outcome.created += 1
                else:
                    for v in doc.versions:
                        if v.status == "active":
                            v.status = "inactive"
                            stale_versions.append(str(v.id))
                    outcome.updated += 1
                doc.file_name = (record.title or record.id)[:500]
                doc.source_uri = record.url
                doc.size_bytes = len(record.text.encode("utf-8"))
                doc.content_fingerprint = fingerprint
                doc.doc_metadata = dict(record.metadata) or None

                version = DocumentVersion(
                    id=uuid.uuid4(),
                    document_id=doc.id,
                    parser_version=RECORD_PARSER_VERSION,
                    chunk_schema_version="1.0",
                    status="processing",
                )
                new_versions.append(version)
                parsed_record = record.to_parsed()
                if strip_boilerplate and not is_system_record_id(record.id):
                    # The document row must exist before its paragraph hashes can point at it.
                    await self._session.flush()
                    parsed_record, boilerplate = await self._strip_boilerplate(
                        corpus_id, doc.id, parsed_record
                    )
                    stripped_blocks += boilerplate.dropped
                chunks = self._chunker.chunk(parsed_record)
                chunk_models = self._build_chunk_models(chunks, version.id, corpus_id)
                all_chunks.extend(chunks)
                all_models.extend(chunk_models)
                owners.extend((doc, version.id, record.metadata) for _ in chunks)

            # Documents first: versions reference them, chunks reference versions.
            await self._session.flush()
            self._session.add_all(new_versions)
            await self._session.flush()
            self._session.add_all(all_models)
            await self._session.flush()
            outcome.chunks_created = len(all_models)

            if all_chunks:
                await self._update_job_status(job, JobStatus.ENRICHING)
                try:
                    enrichments = await self._enricher.enrich_batch(all_chunks)
                except Exception as e:
                    logger.warning("Enrichment failed for record batch, using defaults: %s", e)
                    enrichments = [EnrichmentResult() for _ in all_chunks]
                for chunk_model, chunk_result, enrichment in zip(
                    all_models, all_chunks, enrichments, strict=True
                ):
                    chunk_model.language = enrichment.language
                    self._session.add(
                        ChunkMetadataModel(
                            chunk_id=chunk_model.id,
                            summary=enrichment.summary,
                            keywords=enrichment.keywords,
                            confidence_score=enrichment.confidence_score,
                            table_json=chunk_result.table_json,
                        )
                    )

                await self._update_job_status(job, JobStatus.EMBEDDING)
                t0 = time.monotonic()
                texts = [index_text(c.text, c.structure_path) for c in all_chunks]
                embeddings = await self._embedder.embed_texts(texts)
                self._session.add_all(
                    EmbeddingModel(
                        chunk_id=chunk_model.id,
                        model=emb.model,
                        model_version=emb.model_version,
                        vector_ref=str(chunk_model.id),
                        vector_type="text",
                        dimensions=emb.dimensions,
                    )
                    for chunk_model, emb in zip(all_models, embeddings, strict=True)
                )
                outcome.embed_duration_ms = int((time.monotonic() - t0) * 1000)

                await self._update_job_status(job, JobStatus.STORING)
                t0 = time.monotonic()
                payloads: list[dict[str, Any]] = []
                sparse_vectors: list[SparseVector | None] = []
                for chunk_model, text, chunk_result, enrichment, owner in zip(
                    all_models, texts, all_chunks, enrichments, owners, strict=True
                ):
                    doc, version_id, meta = owner
                    extra = dict(meta)
                    category = extra.pop("category", None)
                    user_id = extra.pop("user_id", None)
                    payload = ChunkPayload(
                        chunk_id=str(chunk_model.id),
                        corpus_id=str(corpus_id),
                        document_id=str(doc.id),
                        document_version_id=str(version_id),
                        page=chunk_result.page,
                        is_table=chunk_result.is_table,
                        is_code=chunk_result.is_code,
                        language=enrichment.language,
                        keywords=enrichment.keywords or [],
                        category=category,
                        user_id=user_id,
                        metadata=extra,
                        text_hash=chunk_model.text_hash,
                    )
                    payloads.append(payload.to_dict())
                    sparse = text_to_sparse_vector(text)
                    sparse_vectors.append(None if sparse.is_empty() else sparse.to_sparse_vector())
                points = _points(
                    all_models, [e.vector for e in embeddings], payloads, sparse_vectors
                )
                for start in range(0, len(points), _UPSERT_BATCH):
                    await self._vector_store.upsert(
                        self._collection, points[start : start + _UPSERT_BATCH]
                    )
                outcome.vectors_stored = len(points)
                dual_target = await self._get_dual_write_target(corpus_id)
                if dual_target is not None:
                    await self._dual_write(dual_target, texts, all_models, payloads, sparse_vectors)
                outcome.store_duration_ms = int((time.monotonic() - t0) * 1000)

            # Old vectors go only after the new ones are in, so a changed record is
            # never missing from search. A failure here leaves stale duplicates, not gaps.
            for start in range(0, len(stale_versions), _UPSERT_BATCH):
                await self._vector_store.delete_by_filter(
                    self._collection,
                    {"document_version_id": stale_versions[start : start + _UPSERT_BATCH]},
                )
            if stale_versions:
                await self._remove_from_dual_write(corpus_id, stale_versions)

            for version in new_versions:
                version.status = "active"
            await self._update_job_status(job, JobStatus.COMPLETED)
            job.processed_documents = len(records)
            job.finished_at = datetime.now(UTC)
            job.review_metadata = {
                "records": {
                    "created": outcome.created,
                    "updated": outcome.updated,
                    "unchanged": outcome.unchanged,
                    "chunks": outcome.chunks_created,
                    **({"stripped_blocks": stripped_blocks} if stripped_blocks else {}),
                }
            }
            await self._session.commit()
            outcome.total_duration_ms = int((time.monotonic() - t_start) * 1000)
            logger.info(
                "Records imported: job_id=%s created=%d updated=%d unchanged=%d chunks=%d "
                "embed_ms=%d store_ms=%d total_ms=%d",
                job_id,
                outcome.created,
                outcome.updated,
                outcome.unchanged,
                outcome.chunks_created,
                outcome.embed_duration_ms,
                outcome.store_duration_ms,
                outcome.total_duration_ms,
            )
            return outcome
        except Exception as e:
            if isinstance(e, DocumentLimitReachedError):
                step = _DOCUMENT_LIMIT_STEP
                logger.warning("Record import job %s refused: %s", job_id, e)
            else:
                step = str(job.status) if job is not None else "unknown"
                logger.error("Record import failed at %s: %s", step, e, exc_info=True)
            failed = PipelineResult(job_id=job_id, status=JobStatus.FAILED, failed_at_step=step)
            await self._handle_failure(job_id, failed, e)
            outcome.status = "failed"
            outcome.error = str(e)
            outcome.failed_at_step = step
            return outcome

    def _transcript_to_parsed(
        self,
        transcript: "TranscriptionResult",
        file_name: str,
        mime_type: str,
    ) -> "ParsedDocument":
        """Convert a transcription result to a ParsedDocument for chunking."""
        from callobuzz_rag.parsing.models import (
            ElementType,
            ParsedDocument,
            ParsedElement,
        )

        metadata = {
            "source_type": "audio_transcription",
            "original_file": file_name,
            "mime_type": mime_type,
            "language_detected": transcript.language_detected,
            "duration_seconds": transcript.duration_seconds,
            "segment_count": len(transcript.segments),
        }

        elements: list[ParsedElement] = []

        if transcript.segments:
            for seg in transcript.segments:
                timestamp = f"[{seg.start:.1f}s - {seg.end:.1f}s]"
                elements.append(
                    ParsedElement(
                        type=ElementType.PARAGRAPH,
                        text=f"{timestamp} {seg.text.strip()}",
                        metadata=metadata,
                    )
                )
        else:
            elements.append(
                ParsedElement(
                    type=ElementType.PARAGRAPH,
                    text=transcript.text,
                    metadata=metadata,
                )
            )

        return ParsedDocument(
            elements=elements,
            source_filename=file_name,
            mime_type=mime_type,
            parser_name="transcription",
            parser_version="1.0",
        )

    async def _handle_failure(
        self, job_id: uuid.UUID, result: PipelineResult, error: Exception
    ) -> PipelineResult:
        """Handle pipeline failure: rollback, update job status."""
        await self._session.rollback()
        result.status = JobStatus.FAILED
        result.error = str(error)
        # A refused job says why in a machine-readable way (code, limit, used, ...).
        coded = (
            error.details()
            if isinstance(
                error, DocumentLimitReachedError | DocumentTooLargeError | NoTextFoundError
            )
            else {}
        )

        try:
            from datetime import UTC, datetime

            now = datetime.now(UTC)
            # Try to persist failure status
            job_update = await self._session.get(IngestJob, job_id)
            if job_update:
                job_update.status = JobStatus.FAILED
                job_update.finished_at = now
                job_update.pending_new_documents = 0  # a failed job reserves no document room
                job_update.errors = {
                    "error": str(error),
                    "failed_at_step": result.failed_at_step,
                    "error_type": type(error).__name__,
                    **coded,
                }
                await self._session.commit()
            else:
                # Job record was part of rolled-back transaction
                new_job = IngestJob(
                    id=job_id,
                    status=JobStatus.FAILED,
                    finished_at=now,
                    errors={
                        "error": str(error),
                        "failed_at_step": result.failed_at_step,
                        **coded,
                    },
                )
                self._session.add(new_job)
                await self._session.commit()
        except Exception:
            logger.warning("Could not persist failure status for job %s", job_id)

        return result

    async def _ensure_document_size(self, corpus_id: uuid.UUID, parsed: "ParsedDocument") -> None:
        """Fail a document whose parsed text is over the token limit (the real count, by
        the chunker's own tokenizer, not bytes: a 5 MB scan can hold a page of text)."""
        if self._document_token_limit is None:
            return
        limit = await self._document_token_limit(self._session, corpus_id)
        if limit is None:
            return
        count = self._chunker.counter.count
        tokens = sum(count(e.text) for e in parsed.elements if e.text)
        if tokens > limit:
            raise DocumentTooLargeError(tokens=tokens, limit=limit)

    async def _ensure_document_room(
        self, corpus_id: uuid.UUID, job_id: uuid.UUID, adding: int
    ) -> None:
        """The injected document-limit backstop (none injected: no check)."""
        if self._document_room is not None and adding > 0:
            await self._document_room(self._session, corpus_id, job_id, adding)

    async def _lock_document(self, document_id: uuid.UUID) -> Document:
        """The document row-locked (SELECT ... FOR UPDATE) with its versions re-read."""
        from sqlalchemy.orm import selectinload

        from callobuzz_rag.core.exceptions import RAGKernelError

        doc = (
            await self._session.execute(
                select(Document)
                .where(Document.id == document_id)
                .with_for_update()
                .options(selectinload(Document.versions))
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if doc is None:
            raise RAGKernelError(f"Document {document_id} was deleted during ingestion")
        return doc

    async def _deactivate_version(self, version: DocumentVersion, corpus_id: uuid.UUID) -> None:
        """Deactivate a document version: mark it inactive and remove its vectors, from the
        corpus's collection and from a running embedding migration's new one."""
        version.status = "inactive"
        await self._drop_version_vectors(str(version.id), corpus_id)

    async def _drop_version_vectors(self, version_id: str, corpus_id: uuid.UUID) -> None:
        """Delete a retired version's points from the corpus's collection and from a running
        embedding migration's new one."""
        await self._vector_store.delete_by_filter(
            self._collection,
            {"document_version_id": version_id},
        )
        await self._remove_from_dual_write(corpus_id, [version_id])

    async def _remove_from_dual_write(self, corpus_id: uuid.UUID, version_ids: list[str]) -> None:
        """Retired versions leave a running migration's new collection too (best effort,
        like every dual-write): a point the migration copied before its version was
        retired would otherwise come back at cutover."""
        if self._settings is None:
            return
        try:
            from callobuzz_rag.migration.context import get_dual_write_collection

            collection = await get_dual_write_collection(self._session, corpus_id)
            if collection is None:
                return
            for start in range(0, len(version_ids), _UPSERT_BATCH):
                await self._vector_store.delete_by_filter(
                    collection, {"document_version_id": version_ids[start : start + _UPSERT_BATCH]}
                )
        except Exception as e:
            logger.warning("Retired version points left in the dual-write collection: %s", e)

    async def _dual_write(
        self,
        target: "DualWriteTarget",
        texts: list[str],
        chunk_models: list[Chunk],
        payloads: list[dict[str, Any]],
        sparse_vectors: list[SparseVector | None],
    ) -> None:
        """The same points and payloads, embedded by the migration's target model, into
        its new collection. Best effort; always closes the target's embedder."""
        try:
            new_embeddings = await target.embedder.embed_texts(texts)
            points = _points(
                chunk_models, [e.vector for e in new_embeddings], payloads, sparse_vectors
            )
            for start in range(0, len(points), _UPSERT_BATCH):
                await self._vector_store.upsert(
                    target.collection, points[start : start + _UPSERT_BATCH]
                )
            self._session.add_all(
                EmbeddingModel(
                    chunk_id=chunk_model.id,
                    model=target.model,
                    model_version=emb.model_version,
                    vector_ref=str(chunk_model.id),
                    vector_type="text",
                    dimensions=emb.dimensions,
                )
                for chunk_model, emb in zip(chunk_models, new_embeddings, strict=True)
            )
        except Exception as e:
            logger.warning("Dual-write to %s failed (best-effort): %s", target.collection, e)
        finally:
            await target.embedder.close()

    def _build_chunk_models(
        self,
        chunks: list[ChunkResult],
        version_id: uuid.UUID,
        corpus_id: uuid.UUID,
    ) -> list[Chunk]:
        """Convert ChunkResults to DB Chunk models, children linked to their parents.

        Ids are assigned here, not by the database: the links are made before anything
        is flushed, and a model's `id` is None until then (the column's default is
        server-side), which is how ingest came to store no parent link at all. The
        chunker puts a parent before its children, so one flush of the list in this
        order satisfies the self-referencing foreign key.
        """
        chunk_models: list[Chunk] = []
        for chunk in chunks:
            model = Chunk(
                id=uuid.uuid4(),
                document_version_id=version_id,
                corpus_id=corpus_id,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
                structure_path=chunk.structure_path,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                page=chunk.page,
                length_tokens=chunk.length_tokens,
                is_table=chunk.is_table,
                is_code=chunk.is_code,
                text_hash=text_hash(chunk.core_text or chunk.text),
            )
            chunk_models.append(model)

        for chunk, model in zip(chunks, chunk_models, strict=True):
            if chunk.parent_chunk_index is not None and chunk.parent_chunk_index < len(
                chunk_models
            ):
                model.parent_chunk_id = chunk_models[chunk.parent_chunk_index].id

        return chunk_models

    async def _boilerplate_scope(self, corpus_id: uuid.UUID) -> tuple[bool, dict[str, Any]]:
        """(whether the cross-document step runs, extra options for the parser).

        On when the engine setting is on and the knowledge base has not opted out. Off, the
        parser is also told not to strip HTML chrome. Without engine settings (a bare
        orchestrator) the step is off and the parser keeps its own default.
        """
        enabled = getattr(self._settings, "boilerplate_strip_enabled", None)
        if not isinstance(enabled, bool):
            return False, {}
        corpus = await self._session.get(Corpus, corpus_id)
        if boilerplate_enabled(self._settings, corpus):
            return True, {}
        return False, {"strip_chrome": False}

    async def _strip_boilerplate(
        self, corpus_id: uuid.UUID, document_id: uuid.UUID, parsed: "ParsedDocument"
    ) -> "tuple[ParsedDocument, BoilerplateOutcome]":
        """The parsed document without the paragraphs the knowledge base already repeats."""
        assert self._settings is not None
        return await apply_boilerplate(
            SqlBlockHashStore(self._session),
            corpus_id,
            document_id,
            parsed,
            min_docs=self._settings.boilerplate_min_docs,
            max_chars=self._settings.boilerplate_max_chars,
            count_tokens=self._chunker.counter.count,
        )

    def _content_sample(self, chunks: list[Any], boilerplate: BoilerplateOutcome) -> list[Any]:
        """The chunks the near-duplicate check compares: spread over the document, and only
        real content. A chunk made only of paragraphs other documents hold (the menu, the
        footer a site repeats) says nothing about whether two pages are the same, so it is
        left out; a document with nothing else is sampled whole."""
        leaves = [c for c in chunks if not c.is_parent]
        shared = boilerplate.shared_hashes | boilerplate.dropped_hashes
        if shared and self._settings is not None:
            max_chars = self._settings.boilerplate_max_chars
            content = [c for c in leaves if not is_shared_chunk(c.text, shared, max_chars)]
            if content:
                leaves = content
        return spread_sample(leaves)

    def _similarity_floor(self) -> float | None:
        """The engine's near-duplicate threshold (None: the function's default)."""
        value = getattr(self._settings, "similarity_min_score", None)
        return float(value) if isinstance(value, int | float) else None

    async def _get_dual_write_target(self, corpus_id: uuid.UUID) -> "DualWriteTarget | None":
        """Check for an active migration requiring dual-write."""
        if self._settings is None:
            return None
        try:
            from callobuzz_rag.migration.context import get_migration_context

            return await get_migration_context(self._session, corpus_id, self._settings)
        except Exception:
            logger.debug("Dual-write check failed (non-fatal)", exc_info=True)
            return None

    async def _update_job_status(self, job: IngestJob, status: JobStatus) -> None:
        """Update job status in DB."""
        job.status = status
        await self._session.flush()

    async def _settle_job_early(
        self,
        job_id: uuid.UUID,
        status: JobStatus,
        *,
        processed: int = 0,
        error: str | None = None,
    ) -> None:
        """Give the job a terminal state when `ingest` returns before the main path.

        The API creates the IngestJob row as `queued` and dispatches to Celery, so the
        row is already there when the pipeline starts. Every early return above used to
        skip it, leaving `{status: queued, processed_documents: 0, finished_at: null}`
        forever — that was BUG-8, most visibly on a duplicate upload, where the caller
        polls a job that has in fact already finished. Any exit from `ingest` has to land
        the job somewhere terminal.
        """
        from datetime import UTC, datetime

        try:
            job = await self._session.get(IngestJob, job_id)
            if job is None:
                job = IngestJob(id=job_id, total_documents=1)
                self._session.add(job)
            job.status = status
            job.processed_documents = processed
            job.finished_at = datetime.now(UTC)
            if error is not None:
                job.errors = {"error": error}
            await self._session.commit()
        except Exception:
            logger.warning("Could not settle job %s as %s", job_id, status, exc_info=True)
