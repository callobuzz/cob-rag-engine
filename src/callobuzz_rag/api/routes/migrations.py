"""Embedding migration routes: read by admin and owning tenant, act by admin only.

A tenant can change a corpus's embedding model (PUT /corpora), which creates a
migration; it can watch that migration here but approving, cancelling, retrying
and rolling back stay with the operator. The embedding catalog is readable by
any key.
"""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import (
    Scope,
    authorized_corpus_ids,
    get_session,
    get_settings,
    require_admin,
    require_admin_or_tenant,
    resolve_scope,
)
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import MigrationError
from callobuzz_rag.core.model_catalog import EmbeddingCatalogEntry
from callobuzz_rag.db.models.corpus_migration import CorpusMigration

logger = logging.getLogger(__name__)

router = APIRouter(tags=["migrations"])


class MigrationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    corpus_id: UUID
    strategy: str
    status: str
    from_config: dict[str, Any]
    to_config: dict[str, Any]
    old_collection: str
    new_collection: str
    chunks_total: int
    chunks_done: int
    memories_total: int
    memories_done: int
    error: str | None = None
    created_at: str | None = None
    approved_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    prune_after: str | None = None


class MigrationListResponse(BaseModel):
    items: list[MigrationResponse]
    total: int
    offset: int
    limit: int


class BulkApproveResponse(BaseModel):
    approved_ids: list[str]
    count: int


def _migration_to_response(m: CorpusMigration) -> MigrationResponse:
    return MigrationResponse(
        id=m.id,
        corpus_id=m.corpus_id,
        strategy=m.strategy,
        status=m.status,
        from_config=m.from_config,
        to_config=m.to_config,
        old_collection=m.old_collection,
        new_collection=m.new_collection,
        chunks_total=m.chunks_total,
        chunks_done=m.chunks_done,
        memories_total=m.memories_total,
        memories_done=m.memories_done,
        error=m.error,
        created_at=m.created_at.isoformat() if m.created_at else None,
        approved_at=m.approved_at.isoformat() if m.approved_at else None,
        started_at=m.started_at.isoformat() if m.started_at else None,
        completed_at=m.completed_at.isoformat() if m.completed_at else None,
        prune_after=m.prune_after.isoformat() if m.prune_after else None,
    )


@router.get("/api/v1/migrations", response_model=MigrationListResponse)
async def list_migrations(
    request: Request,
    corpus_id: UUID | None = Query(None, description="Only this corpus's migrations."),
    status: str | None = Query(None, description="Only migrations in this status."),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    _auth: None = Depends(require_admin_or_tenant),
) -> MigrationListResponse:
    """Migrations across corpora, newest first: admin all, a tenant its own corpora.

    `pending_approval` rows are what the nightly drift check or an embedding
    change created and an admin has yet to approve.
    """
    allowed = await authorized_corpus_ids(resolve_scope(request), corpus_id, session)
    where: list[ColumnElement[bool]] = []
    if allowed is not None:
        where.append(CorpusMigration.corpus_id.in_(allowed))
    if status is not None:
        where.append(CorpusMigration.status == status)
    total = (
        await session.execute(select(func.count(CorpusMigration.id)).where(*where))
    ).scalar() or 0
    result = await session.execute(
        select(CorpusMigration)
        .where(*where)
        .order_by(CorpusMigration.created_at.desc(), CorpusMigration.id)
        .offset(offset)
        .limit(limit)
    )
    return MigrationListResponse(
        items=[_migration_to_response(m) for m in result.scalars().all()],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/api/v1/corpora/{corpus_id}/migrations", response_model=list[MigrationResponse])
async def list_corpus_migrations(
    corpus_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _auth: None = Depends(require_admin_or_tenant),
) -> list[MigrationResponse]:
    """List all migrations for a corpus. Admin, or the tenant that owns it."""
    await authorized_corpus_ids(resolve_scope(request), corpus_id, session)
    stmt = (
        select(CorpusMigration)
        .where(CorpusMigration.corpus_id == corpus_id)
        .order_by(CorpusMigration.created_at.desc())
    )
    result = await session.execute(stmt)
    return [_migration_to_response(m) for m in result.scalars().all()]


@router.get("/api/v1/migrations/{migration_id}", response_model=MigrationResponse)
async def get_migration(
    migration_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _auth: None = Depends(require_admin_or_tenant),
) -> MigrationResponse:
    """Get migration status and progress. Admin, or the tenant that owns the corpus."""
    migration = await session.get(CorpusMigration, migration_id)
    if migration is None:
        raise HTTPException(status_code=404, detail="Migration not found")
    await authorized_corpus_ids(resolve_scope(request), migration.corpus_id, session)
    return _migration_to_response(migration)


@router.post("/api/v1/migrations/{migration_id}/approve", response_model=MigrationResponse)
async def approve_migration(
    migration_id: UUID,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _admin: None = Depends(require_admin),
) -> MigrationResponse:
    """Approve a pending migration and dispatch the worker task."""
    from callobuzz_rag.migration.service import approve_migration as svc_approve

    try:
        migration = await svc_approve(session, migration_id)
        await session.commit()
    except MigrationError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    try:
        from callobuzz_rag.worker.tasks import migrate_corpus_task

        migrate_corpus_task.delay(str(migration.id))
    except Exception:
        logger.warning("Failed to dispatch migration task %s", migration_id, exc_info=True)

    return _migration_to_response(migration)


@router.post("/api/v1/migrations/{migration_id}/cancel", response_model=MigrationResponse)
async def cancel_migration(
    migration_id: UUID,
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> MigrationResponse:
    """Cancel a migration from pending_approval, queued, or running."""
    from callobuzz_rag.migration.service import cancel_migration as svc_cancel

    try:
        migration = await svc_cancel(session, migration_id)
        await session.commit()
    except MigrationError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _migration_to_response(migration)


@router.post("/api/v1/migrations/{migration_id}/retry", response_model=MigrationResponse)
async def retry_migration(
    migration_id: UUID,
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> MigrationResponse:
    """Retry a failed migration."""
    from callobuzz_rag.migration.service import retry_migration as svc_retry

    try:
        migration = await svc_retry(session, migration_id)
        await session.commit()
    except MigrationError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    # Determine direction: if the corpus already points to new_collection,
    # this was a failed rollback — dispatch with rollback=True
    from callobuzz_rag.db.models.corpus import Corpus

    corpus = await session.get(Corpus, migration.corpus_id)
    is_rollback = corpus is not None and corpus.qdrant_collection == migration.new_collection

    try:
        from callobuzz_rag.worker.tasks import migrate_corpus_task

        migrate_corpus_task.delay(str(migration.id), rollback=is_rollback)
    except Exception:
        logger.warning("Failed to dispatch retry task %s", migration_id, exc_info=True)

    return _migration_to_response(migration)


@router.post("/api/v1/migrations/{migration_id}/rollback", response_model=MigrationResponse)
async def rollback_migration(
    migration_id: UUID,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    _admin: None = Depends(require_admin),
) -> MigrationResponse:
    """Start rollback of a completed migration."""
    from callobuzz_rag.migration.service import start_rollback

    try:
        migration = await start_rollback(session, migration_id, settings)
        await session.commit()
    except MigrationError as e:
        status = 410 if "expired" in str(e).lower() else 409
        raise HTTPException(status_code=status, detail=str(e)) from e

    try:
        from callobuzz_rag.worker.tasks import migrate_corpus_task

        migrate_corpus_task.delay(str(migration.id), rollback=True)
    except Exception:
        logger.warning("Failed to dispatch rollback task %s", migration_id, exc_info=True)

    return _migration_to_response(migration)


@router.post("/api/v1/admin/migrations/approve-all", response_model=BulkApproveResponse)
async def bulk_approve_migrations(
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> BulkApproveResponse:
    """Approve all pending migrations (engine-default change flow)."""
    from callobuzz_rag.migration.service import bulk_approve

    approved_ids = await bulk_approve(session)
    await session.commit()

    for mid in approved_ids:
        try:
            from callobuzz_rag.worker.tasks import migrate_corpus_task

            migrate_corpus_task.delay(str(mid))
        except Exception:
            logger.warning("Failed to dispatch migration task %s", mid, exc_info=True)

    return BulkApproveResponse(
        approved_ids=[str(i) for i in approved_ids],
        count=len(approved_ids),
    )


@router.get("/api/v1/models/embeddings")
async def list_embedding_models(
    _scope: Scope = Depends(resolve_scope),
) -> list[EmbeddingCatalogEntry]:
    """The embedding models a corpus can use. Any key: it is the menu, not a secret."""
    from callobuzz_rag.core.model_catalog import EMBEDDING_CATALOG

    return list(EMBEDDING_CATALOG)
