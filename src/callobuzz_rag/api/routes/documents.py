"""Document management endpoints -- listing, version history, merge, restore.

Every route acts on one corpus: a corpus key's own, or the `?corpus_id=` an admin
or tenant key names (see `api.deps.scoped_corpus`).
"""

import logging
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from callobuzz_rag.accounts.caps import check_document_room
from callobuzz_rag.api.deps import get_session, require_owner, scoped_corpus
from callobuzz_rag.api.errors import APIError  # BIZ3 source
from callobuzz_rag.api.schemas.delete_preview import DeletePreviewResponse
from callobuzz_rag.api.schemas.documents import (
    ChunkView,
    DocumentDetailResponse,
    DocumentListResponse,
    DocumentSummary,
    MergeResponse,
    RestoreResponse,
    VersionListResponse,
    VersionSummary,
)
from callobuzz_rag.api.services.document_removal import (
    dual_delete as _dual_delete,
)
from callobuzz_rag.api.services.document_removal import (
    dual_write_collection as _dual_write_collection,
)
from callobuzz_rag.api.services.document_removal import remove_document
from callobuzz_rag.api.services.system_documents import (
    refuse_system_document as _refuse_system_document,
)
from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])


# BIZ3 source
def _refuse_written_text(*docs: Document) -> None:
    """Articles and FAQ entries are changed in Knowledge: the editor keeps their text."""
    if any((d.external_id or "").startswith(("article:", "faq:")) for d in docs):
        raise APIError(
            409,
            "Edit the article in Knowledge instead.",
            "article_edit_instead",
        )


# /BIZ3 source


async def _chunk_counts(session: AsyncSession, version_ids: list[UUID]) -> dict[UUID, int]:
    """Chunks per version, counted in the database (not by loading every chunk)."""
    if not version_ids:
        return {}
    rows = await session.execute(
        select(Chunk.document_version_id, func.count(Chunk.id))
        .where(Chunk.document_version_id.in_(version_ids))
        .group_by(Chunk.document_version_id)
    )
    return {row[0]: row[1] for row in rows.all()}


def _version_summaries(
    versions: list[DocumentVersion], counts: dict[UUID, int]
) -> list[VersionSummary]:
    return [
        VersionSummary(
            id=v.id,
            status=v.status,
            chunk_count=counts.get(v.id, 0),
            parser_version=v.parser_version,
            chunk_schema_version=v.chunk_schema_version,
            created_at=v.created_at,
        )
        for v in sorted(versions, key=lambda x: x.created_at, reverse=True)
    ]


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    request: Request,
    search: str | None = Query(
        None,
        max_length=200,
        description="Case-insensitive part of the file name or external id.",
    ),
    # BIZ3 source
    source: Literal["website", "articles", "files"] | None = Query(
        None,
        description="Where a document came from: learned from the website, written as an "
        "article or FAQ, or uploaded as a file.",
    ),
    # /BIZ3 source
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(scoped_corpus),
) -> DocumentListResponse:
    """List a corpus's documents, newest first; `count` is the total across pages."""
    where = [Document.corpus_id == corpus.id]
    # BIZ3 source
    if source == "website":
        where.append(Document.external_id.like("web:%"))
    elif source == "articles":
        where.append(
            or_(Document.external_id.like("article:%"), Document.external_id.like("faq:%"))
        )
    elif source == "files":
        where.append(Document.external_id.is_(None))
    # /BIZ3 source
    if search:
        pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where.append(
            or_(
                Document.file_name.ilike(pattern, escape="\\"),
                Document.external_id.ilike(pattern, escape="\\"),
            )
        )
    result = await session.execute(
        select(Document)
        .where(*where)
        .options(selectinload(Document.versions))
        .order_by(Document.ingested_at.desc(), Document.id)
        .limit(limit)
        .offset(offset)
    )
    docs = result.scalars().all()

    count_result = await session.execute(select(func.count(Document.id)).where(*where))
    total = count_result.scalar() or 0

    items = []
    for doc in docs:
        active_version = next((v for v in doc.versions if v.status == "active"), None)
        items.append(
            DocumentSummary(
                id=doc.id,
                file_name=doc.file_name,
                mime_type=doc.mime_type,
                size_bytes=doc.size_bytes,
                ingested_at=doc.ingested_at,
                active_version_id=active_version.id if active_version else None,
                version_count=len(doc.versions),
                external_id=doc.external_id,
                metadata=doc.doc_metadata,
            )
        )

    return DocumentListResponse(items=items, count=total)


@router.get("/{document_id}", response_model=DocumentDetailResponse)
async def get_document(
    document_id: UUID,
    version_id: UUID | None = Query(
        None, description="Show this version's chunks instead of the active version's."
    ),
    chunk_offset: int = Query(0, ge=0),
    chunk_limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(scoped_corpus),
) -> DocumentDetailResponse:
    """A document, its versions, and the chunks of its active (or given) version. Read-only."""
    result = await session.execute(
        select(Document)
        .where(Document.id == document_id, Document.corpus_id == corpus.id)
        .options(selectinload(Document.versions))
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    versions = list(doc.versions)
    active = next((v for v in versions if v.status == "active"), None)
    if version_id is not None:
        shown = next((v for v in versions if v.id == version_id), None)
        if shown is None:
            raise HTTPException(status_code=404, detail="Version not found")
    else:
        shown = active

    counts = await _chunk_counts(session, [v.id for v in versions])
    chunks: list[Chunk] = []
    if shown is not None:
        rows = await session.execute(
            select(Chunk)
            .where(Chunk.document_version_id == shown.id)
            .order_by(Chunk.chunk_index.asc().nulls_last(), Chunk.id)
            .offset(chunk_offset)
            .limit(chunk_limit)
        )
        chunks = list(rows.scalars().all())

    return DocumentDetailResponse(
        id=doc.id,
        file_name=doc.file_name,
        mime_type=doc.mime_type,
        size_bytes=doc.size_bytes,
        ingested_at=doc.ingested_at,
        external_id=doc.external_id,
        metadata=doc.doc_metadata,
        source_uri=doc.source_uri,
        active_version_id=active.id if active else None,
        versions=_version_summaries(versions, counts),
        version_id=shown.id if shown else None,
        chunks=[
            ChunkView(
                id=c.id,
                chunk_index=c.chunk_index,
                text=c.text,
                section=c.structure_path,
                page=c.page,
                token_count=c.length_tokens,
                is_table=bool(c.is_table),
                is_code=bool(c.is_code),
                parent_chunk_id=c.parent_chunk_id,
            )
            for c in chunks
        ],
        chunk_total=counts.get(shown.id, 0) if shown else 0,
        chunk_offset=chunk_offset,
        chunk_limit=chunk_limit,
    )


@router.get("/{document_id}/versions", response_model=VersionListResponse)
async def list_versions(
    request: Request,
    document_id: UUID,
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(scoped_corpus),
) -> VersionListResponse:
    """List all versions of a document."""
    result = await session.execute(
        select(Document)
        .where(Document.id == document_id, Document.corpus_id == corpus.id)
        .options(selectinload(Document.versions))
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    counts = await _chunk_counts(session, [v.id for v in doc.versions])
    versions = _version_summaries(list(doc.versions), counts)
    return VersionListResponse(
        document_id=doc.id,
        file_name=doc.file_name,
        versions=versions,
        count=len(versions),
    )


@router.post("/{new_document_id}/replace/{old_document_id}", response_model=MergeResponse)
async def merge_documents(
    request: Request,
    new_document_id: UUID,
    old_document_id: UUID,
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(scoped_corpus),
    _owner: None = Depends(require_owner),
) -> MergeResponse:
    """Merge a new document into an old document's version stack.

    Moves the new document's active version under the old document,
    deactivates the old document's active version, and deletes the
    now-empty new document record.
    """
    # Both documents locked, in id order, before any version changes: a concurrent
    # ingest, restore or review finalize of either waits, so each keeps one active version.
    await session.execute(
        select(Document.id)
        .where(Document.id.in_([old_document_id, new_document_id]))
        .order_by(Document.id)
        .with_for_update()
    )
    old_result = await session.execute(
        select(Document)
        .where(Document.id == old_document_id, Document.corpus_id == corpus.id)
        .options(selectinload(Document.versions))
        .execution_options(populate_existing=True)
    )
    old_doc = old_result.scalar_one_or_none()
    if not old_doc:
        raise HTTPException(status_code=404, detail="Old document not found")

    new_result = await session.execute(
        select(Document)
        .where(Document.id == new_document_id, Document.corpus_id == corpus.id)
        .options(selectinload(Document.versions))
        .execution_options(populate_existing=True)
    )
    new_doc = new_result.scalar_one_or_none()
    if not new_doc:
        raise HTTPException(status_code=404, detail="New document not found")

    if old_document_id == new_document_id:
        raise HTTPException(status_code=422, detail="Cannot merge document with itself")
    _refuse_system_document(old_doc, new_doc)
    _refuse_written_text(old_doc, new_doc)  # BIZ3 source

    # Deactivate old document's active versions
    vector_store = request.app.state.vector_store
    collection = corpus.qdrant_collection or "chunks"
    dual = await _dual_write_collection(session, corpus.id)

    for v in old_doc.versions:
        if v.status == "active":
            v.status = "inactive"
            await vector_store.delete_by_filter(collection, {"document_version_id": str(v.id)})
            await _dual_delete(vector_store, dual, {"document_version_id": str(v.id)})
    # Retired before the new document's active version moves in (one active per document).
    await session.flush()

    # Move new document's versions under old document and update Qdrant payload
    for v in new_doc.versions:
        v.document_id = old_doc.id
        # Update Qdrant payload so document_id matches the merged target
        for target in (collection, dual):
            if target is None:
                continue
            try:
                await vector_store.update_payload(
                    target,
                    {"document_version_id": str(v.id)},
                    {"document_id": str(old_doc.id)},
                )
            except Exception as e:
                if target == collection:
                    raise
                logger.warning("Dual-write collection %s keeps old document ids: %s", target, e)

    # Update old document metadata from new
    old_doc.content_fingerprint = new_doc.content_fingerprint
    old_doc.size_bytes = new_doc.size_bytes
    old_doc.mime_type = new_doc.mime_type

    total_versions = len(old_doc.versions) + len(new_doc.versions)

    # Delete the now-empty new document
    await session.delete(new_doc)
    await session.commit()

    return MergeResponse(
        old_document_id=old_doc.id,
        new_document_id=new_document_id,
        versions_preserved=total_versions,
    )


@router.get("/{document_id}/delete-preview", response_model=DeletePreviewResponse)
async def preview_delete_document(
    document_id: UUID,
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(scoped_corpus),
) -> DeletePreviewResponse:
    """What deleting this document would remove (versions, chunks) and the name to confirm
    with. Changes nothing."""
    from callobuzz_rag.api.services.delete_guard import document_counts, document_display_name

    doc = (
        await session.execute(
            select(Document).where(Document.id == document_id, Document.corpus_id == corpus.id)
        )
    ).scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    name = document_display_name(doc)
    return DeletePreviewResponse(
        kind="document",
        id=doc.id,
        name=name,
        confirm_name=name,
        counts=await document_counts(session, doc.id),
    )


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    request: Request,
    document_id: UUID,
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(scoped_corpus),
    _owner: None = Depends(require_owner),
    confirm_name: str | None = Query(
        None,
        max_length=500,
        description="The document's file name (or record id), typed exactly. Required with an "
        "API key (422 confirmation_required / confirm_mismatch); a dashboard session confirms "
        "in the UI. GET /documents/{id}/delete-preview says what would be removed.",
    ),
) -> None:
    """Delete a document, all its versions, chunks, and vectors.

    Purges the document's vectors from Qdrant (every version) and deletes the
    Document row, which cascades to versions -> chunks -> embeddings and chunk
    metadata in Postgres. Idempotent re-ingestion (e.g. after a chunker change)
    relies on this: the pipeline dedups byte-identical content by fingerprint,
    so a document must be removed before it can be re-ingested fresh.
    """
    result = await session.execute(
        select(Document)
        .where(Document.id == document_id, Document.corpus_id == corpus.id)
        .options(selectinload(Document.versions))
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    # COB9 platform docs (start)
    from callobuzz_rag.cob.system import is_system_corpus

    system_kb = is_system_corpus(corpus)
    if system_kb:  # platform docs can be deleted, built-in ones never
        from callobuzz_rag.cob.platform import refuse_builtin

        refuse_builtin(doc.external_id, doc.doc_metadata)
    # COB9 platform docs (end)
    else:
        _refuse_system_document(doc)
    from callobuzz_rag.api.services.delete_guard import (
        document_display_name,
        needs_confirm_name,
        require_confirm_name,
    )

    if needs_confirm_name(request):
        require_confirm_name(request, confirm_name, document_display_name(doc), "document")

    await remove_document(session, request.app.state.vector_store, corpus, doc)
    # COB9 platform docs (start)
    if system_kb:
        from callobuzz_rag.accounts.audit import record_audit

        await record_audit(
            session,
            request,
            "cob.platform_doc_deleted",
            target_type="document",
            target_id=str(doc.id),
            details={"name": doc.file_name},
        )
    # COB9 platform docs (end)
    # BIZ3 source: an article's or FAQ entry's written text goes with its document
    if (doc.external_id or "").startswith(("article:", "faq:")):
        from sqlalchemy import delete

        from callobuzz_rag.db.models.knowledge_article import KnowledgeArticle

        await session.execute(
            delete(KnowledgeArticle).where(
                KnowledgeArticle.corpus_id == corpus.id,
                KnowledgeArticle.external_id == doc.external_id,
            )
        )
    # /BIZ3 source
    await session.commit()


async def _restore_into_dual_write(
    session: AsyncSession,
    request: Request,
    corpus_id: UUID,
    chunks: Any,
    texts: list[str],
    points: Any,
) -> None:
    """The restored points, embedded by a running migration's target model, into its new
    collection, with the audit rows its reconciliation counts. Best effort."""
    from callobuzz_rag.db.models.embedding import Embedding
    from callobuzz_rag.migration.context import get_migration_context
    from callobuzz_rag.vectordb.base import VectorPoint

    try:
        target = await get_migration_context(session, corpus_id, request.app.state.settings)
    except Exception as e:
        logger.warning("Restored version not dual-written: %s", e)
        return
    if target is None:
        return
    try:
        vectors = await target.embedder.embed_texts(texts)
        await request.app.state.vector_store.upsert(
            target.collection,
            [
                VectorPoint(
                    id=p.id, vector=v.vector, payload=p.payload, sparse_vector=p.sparse_vector
                )
                for p, v in zip(points, vectors, strict=True)
            ],
        )
        audited = set(
            (
                await session.execute(
                    select(Embedding.chunk_id).where(
                        Embedding.chunk_id.in_([c.id for c in chunks]),
                        Embedding.model == target.model,
                    )
                )
            )
            .scalars()
            .all()
        )
        session.add_all(
            Embedding(
                chunk_id=c.id,
                model=target.model,
                model_version=v.model_version,
                vector_ref=str(c.id),
                vector_type="text",
                dimensions=v.dimensions,
            )
            for c, v in zip(chunks, vectors, strict=True)
            if c.id not in audited
        )
    except Exception as e:
        logger.warning("Dual-write of a restored version to %s failed: %s", target.collection, e)
    finally:
        await target.embedder.close()


@router.post(
    "/{document_id}/versions/{version_id}/restore",
    response_model=RestoreResponse,
)
async def restore_version(
    request: Request,
    document_id: UUID,
    version_id: UUID,
    session: AsyncSession = Depends(get_session),
    corpus: Corpus = Depends(scoped_corpus),
    _owner: None = Depends(require_owner),
) -> RestoreResponse:
    """Restore an old version of a document.

    Re-embeds the old version's chunks and inserts them into Qdrant, then deactivates the
    currently active version and deletes its points. All-or-nothing: the old version is
    validated (it exists, belongs to the document, has chunks) and embedded first, its points
    go in before the current version's are removed, and the current version's points are
    deleted only after the database commit; on any failure the current version stays active
    and searchable. Owner-only, like deleting a document:
    it retires the version people are answered from (accounts A8).
    """
    result = await session.execute(
        select(Document)
        .where(Document.id == document_id, Document.corpus_id == corpus.id)
        .options(selectinload(Document.versions))
        # Locked, versions re-read: a concurrent ingest, restore or review finalize of
        # this document waits, so exactly one version ends up active.
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    _refuse_system_document(doc)
    _refuse_written_text(doc)  # BIZ3 source

    target_version = next((v for v in doc.versions if v.id == version_id), None)
    if not target_version:
        raise HTTPException(status_code=404, detail="Version not found")

    if target_version.status == "active":
        raise HTTPException(status_code=409, detail="Version is already active")

    current_active = next((v for v in doc.versions if v.status == "active"), None)
    previous_active_id = current_active.id if current_active else None

    # Active-document limit (accounts A6): restoring a document that has no active
    # version makes it count again; switching versions of an active one adds nothing.
    await check_document_room(session, corpus.tenant_id, 0 if current_active else 1)

    # Everything that can fail is checked BEFORE anything is changed: the old version's
    # chunks, the embedder, the embeddings. Then the restored version's vectors go in
    # while the current version's are still there, the database is committed, and only
    # then are the current version's vectors deleted. Any failure before the commit
    # leaves the current version active and fully searchable.
    chunks_result = await session.execute(
        select(Chunk)
        .where(Chunk.document_version_id == version_id)
        .options(selectinload(Chunk.metadata_record))
    )
    chunks = chunks_result.scalars().all()

    if not chunks:
        raise HTTPException(status_code=422, detail="Version has no chunks to restore")

    from callobuzz_rag.chunking.index_text import index_text
    from callobuzz_rag.embeddings.factory import embedder_for_corpus

    default_embedder = request.app.state.embedder
    if default_embedder is None:
        raise HTTPException(status_code=503, detail="Embedding provider unavailable")
    embedder = embedder_for_corpus(corpus, request.app.state.settings, default_embedder)

    chunk_texts = [index_text(c.text, c.structure_path) for c in chunks]
    embeddings = await embedder.embed_texts(chunk_texts)

    from callobuzz_rag.pipeline.payload import chunk_payload
    from callobuzz_rag.retrieval.sparse import text_to_sparse_vector
    from callobuzz_rag.vectordb.base import VectorPoint

    points = []
    for chunk, emb, text in zip(chunks, embeddings, chunk_texts, strict=True):
        payload = chunk_payload(
            chunk,
            corpus.id,
            document_id,
            doc.doc_metadata,
            chunk.metadata_record.keywords if chunk.metadata_record else None,
        )
        sparse_data = text_to_sparse_vector(text)
        sparse_vec = sparse_data.to_sparse_vector() if not sparse_data.is_empty() else None

        points.append(
            VectorPoint(
                id=str(chunk.id),
                vector=emb.vector,
                payload=payload.to_dict(),
                sparse_vector=sparse_vec,
            )
        )

    vector_store = request.app.state.vector_store
    collection = corpus.qdrant_collection or "chunks"
    dual = await _dual_write_collection(session, corpus.id)
    restored_filter = {"document_version_id": str(version_id)}

    async def _remove_restored_points() -> None:
        """Undo the restored version's points (it was inactive: it had none before)."""
        try:
            await vector_store.delete_by_filter(collection, restored_filter)
        except Exception:
            logger.error("Could not remove the points of version %s", version_id, exc_info=True)
        await _dual_delete(vector_store, dual, restored_filter)

    try:
        await vector_store.upsert(collection, points)
        if dual is not None:
            await _restore_into_dual_write(session, request, corpus.id, chunks, chunk_texts, points)
        if current_active:
            current_active.status = "inactive"
            await session.flush()  # retired before the restored version becomes active
        target_version.status = "active"
        await session.commit()
    except BaseException:
        await session.rollback()
        # The rollback restored the versions' statuses in the database; the objects follow.
        target_version.status = "inactive"
        if current_active:
            current_active.status = "active"
        await _remove_restored_points()
        raise

    # Committed: the restored version is the active one. Its predecessor's points are now
    # stale; a failure here leaves them behind (logged), never the document unsearchable.
    if current_active:
        stale = {"document_version_id": str(current_active.id)}
        try:
            await vector_store.delete_by_filter(collection, stale)
        except Exception:
            logger.error(
                "Version %s was restored but the previous version's points %s could not be "
                "removed from %s; delete them by hand",
                version_id,
                stale,
                collection,
                exc_info=True,
            )
        await _dual_delete(vector_store, dual, stale)

    return RestoreResponse(
        document_id=document_id,
        restored_version_id=version_id,
        previous_active_version_id=previous_active_id,
    )
