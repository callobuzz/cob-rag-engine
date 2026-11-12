"""Migration service — create, approve, cancel, retry, rollback, drift detection."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.config_resolver import ConfigResolver, ResolvedEmbeddingConfig
from callobuzz_rag.core.exceptions import MigrationError
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.corpus_migration import CorpusMigration

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "rolled_back", "cancelled", "failed"}
NON_TERMINAL_STATUSES = {"pending_approval", "queued", "running", "verifying", "rolling_back"}


def _config_snapshot(resolved: ResolvedEmbeddingConfig) -> dict[str, Any]:
    return {
        "provider": resolved.provider,
        "model": resolved.model,
        "dimensions": resolved.dimensions,
    }


def _next_collection_version(corpus: Corpus) -> str:
    """Derive the next versioned collection name."""
    current = corpus.qdrant_collection
    id_short = str(corpus.id)[:8]
    prefix = f"corpus_{id_short}_v"
    if prefix in current:
        try:
            n = int(current.split("_v")[-1])
        except (ValueError, IndexError):
            n = 0
        return f"corpus_{id_short}_v{n + 1}"
    return f"corpus_{id_short}_v1"


def effective_desired(corpus: Corpus, settings: Settings) -> ResolvedEmbeddingConfig:
    """Compute what this corpus should be running: corpus config or engine default."""
    from callobuzz_rag.core.secrets import encryptor_from_settings

    # With the encryptor, a corpus's own hosted-embedding key is used; without it
    # the stored key was never decrypted and re-embedding ran on the engine key.
    resolver = ConfigResolver(settings, encryptor=encryptor_from_settings(settings))
    return resolver.resolve_embedding(corpus.embedding_config)


def is_drifted(corpus: Corpus, settings: Settings) -> bool:
    """True when effective desired != applied (drift rule from §3)."""
    # NULL means the collection was built with the engine's configured model (what
    # create and the phase8_003/phase10_002 backfills record). Treating it as
    # "drifted" queued a re-embed onto the same model for every such corpus.
    applied = corpus.applied_embedding or {
        "provider": settings.embedding_provider,
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dimensions,
    }
    desired = effective_desired(corpus, settings)
    return (
        applied.get("provider") != desired.provider
        or applied.get("model") != desired.model
        or applied.get("dimensions") != desired.dimensions
    )


async def get_non_terminal_migration(
    session: AsyncSession, corpus_id: uuid.UUID
) -> CorpusMigration | None:
    """Return the active (non-terminal) migration for a corpus, if any."""
    stmt = select(CorpusMigration).where(
        CorpusMigration.corpus_id == corpus_id,
        CorpusMigration.status.in_(NON_TERMINAL_STATUSES),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_migration(
    session: AsyncSession,
    corpus: Corpus,
    settings: Settings,
) -> CorpusMigration:
    """Create a pending_approval migration for a drifted corpus.

    Supersedes any existing pending_approval/queued migration in one transaction.
    Raises MigrationError if a running/verifying/rolling_back migration exists.
    """
    existing = await get_non_terminal_migration(session, corpus.id)
    if existing:
        if existing.status in ("running", "verifying", "rolling_back"):
            raise MigrationError(
                f"Cannot create migration: corpus has an active {existing.status} migration "
                f"({existing.id}). Cancel it first."
            )
        existing.status = "cancelled"

    desired = effective_desired(corpus, settings)
    applied = corpus.applied_embedding or {}
    from_config: dict[str, Any] = {
        "provider": applied.get("provider", settings.embedding_provider),
        "model": applied.get("model", settings.embedding_model),
        "dimensions": applied.get("dimensions", settings.embedding_dimensions),
    }
    to_config = _config_snapshot(desired)

    migration = CorpusMigration(
        corpus_id=corpus.id,
        strategy="re_embed",
        status="pending_approval",
        from_config=from_config,
        to_config=to_config,
        old_collection=corpus.qdrant_collection,
        new_collection=_next_collection_version(corpus),
        chunks_total=0,
        chunks_done=0,
        memories_total=0,
        memories_done=0,
    )
    session.add(migration)
    await session.flush()
    logger.info(
        "Created migration %s for corpus %s: %s -> %s",
        migration.id,
        corpus.id,
        from_config.get("model"),
        to_config.get("model"),
    )
    return migration


async def approve_migration(session: AsyncSession, migration_id: uuid.UUID) -> CorpusMigration:
    """Transition pending_approval -> queued and return the migration."""
    migration = await session.get(CorpusMigration, migration_id)
    if migration is None:
        raise MigrationError(f"Migration {migration_id} not found")
    if migration.status != "pending_approval":
        raise MigrationError(
            f"Cannot approve migration in status '{migration.status}' (expected pending_approval)"
        )
    migration.status = "queued"
    migration.approved_at = datetime.now(UTC)
    await session.flush()
    logger.info("Approved migration %s", migration_id)
    return migration


async def cancel_migration(session: AsyncSession, migration_id: uuid.UUID) -> CorpusMigration:
    """Cancel from pending_approval, queued, or running."""
    migration = await session.get(CorpusMigration, migration_id)
    if migration is None:
        raise MigrationError(f"Migration {migration_id} not found")
    if migration.status not in ("pending_approval", "queued", "running"):
        raise MigrationError(f"Cannot cancel migration in status '{migration.status}'")
    old_status = migration.status
    migration.status = "cancelled"
    logger.info("Cancelled migration %s (was %s)", migration_id, old_status)
    return migration


async def retry_migration(session: AsyncSession, migration_id: uuid.UUID) -> CorpusMigration:
    """Retry a failed migration — resumes forward or rollback direction."""
    migration = await session.get(CorpusMigration, migration_id)
    if migration is None:
        raise MigrationError(f"Migration {migration_id} not found")
    if migration.status != "failed":
        raise MigrationError(
            f"Cannot retry migration in status '{migration.status}' (expected failed)"
        )
    migration.status = "queued"
    migration.error = None
    logger.info("Retried migration %s", migration_id)
    return migration


async def start_rollback(
    session: AsyncSession,
    migration_id: uuid.UUID,
    settings: Settings,
) -> CorpusMigration:
    """Start rollback of a completed migration.

    Eligibility: most recent completed, still the live collection, before prune_after.
    """
    migration = await session.get(CorpusMigration, migration_id)
    if migration is None:
        raise MigrationError(f"Migration {migration_id} not found")
    if migration.status != "completed":
        raise MigrationError(
            f"Cannot rollback migration in status '{migration.status}' (expected completed)"
        )

    corpus = await session.get(Corpus, migration.corpus_id)
    if corpus is None:
        raise MigrationError("Corpus not found")

    if corpus.qdrant_collection != migration.new_collection:
        raise MigrationError(
            "This migration's result is no longer the live collection — "
            "a newer migration has superseded it."
        )

    if migration.prune_after and datetime.now(UTC) > migration.prune_after:
        raise MigrationError(
            "Rollback window expired (old collection may have been pruned). "
            "Run a fresh migration instead."
        )

    migration.status = "rolling_back"
    logger.info("Started rollback for migration %s", migration_id)
    return migration


async def complete_cutover(
    session: AsyncSession,
    migration: CorpusMigration,
    settings: Settings,
) -> None:
    """Atomic cutover: flip pointer, stamp applied, swap memories."""
    corpus = await session.get(Corpus, migration.corpus_id)
    if corpus is None:
        raise MigrationError("Corpus deleted during migration")

    corpus.qdrant_collection = migration.new_collection
    corpus.applied_embedding = migration.to_config
    migration.status = "completed"
    migration.completed_at = datetime.now(UTC)
    migration.prune_after = datetime.now(UTC) + timedelta(days=settings.migration_retention_days)

    from callobuzz_rag.db.models.memory import Memory

    await session.execute(
        update(Memory)
        .where(
            Memory.corpus_id == migration.corpus_id,
            Memory.embedding_staged.isnot(None),
        )
        .values(
            embedding=Memory.embedding_staged,
            embedding_model=migration.to_config["model"],
            embedding_staged=None,
        )
    )
    await session.flush()
    logger.info(
        "Cutover complete for migration %s: collection=%s",
        migration.id,
        migration.new_collection,
    )


async def complete_rollback(
    session: AsyncSession,
    migration: CorpusMigration,
    settings: Settings,
) -> None:
    """Atomic rollback: flip pointer back, stamp applied, swap memories, pin config."""
    corpus = await session.get(Corpus, migration.corpus_id)
    if corpus is None:
        raise MigrationError("Corpus deleted during rollback")

    corpus.qdrant_collection = migration.old_collection
    corpus.applied_embedding = migration.from_config
    corpus.embedding_config = migration.from_config
    migration.status = "rolled_back"
    migration.completed_at = datetime.now(UTC)
    migration.prune_after = datetime.now(UTC) + timedelta(days=settings.migration_retention_days)

    from callobuzz_rag.db.models.memory import Memory

    await session.execute(
        update(Memory)
        .where(
            Memory.corpus_id == migration.corpus_id,
            Memory.embedding_staged.isnot(None),
        )
        .values(
            embedding=Memory.embedding_staged,
            embedding_model=migration.from_config["model"],
            embedding_staged=None,
        )
    )
    await session.flush()
    logger.info(
        "Rollback complete for migration %s: collection=%s",
        migration.id,
        migration.old_collection,
    )


async def bulk_approve(session: AsyncSession) -> list[uuid.UUID]:
    """Approve all pending_approval migrations (engine-default change flow)."""
    stmt = select(CorpusMigration).where(CorpusMigration.status == "pending_approval")
    result = await session.execute(stmt)
    migrations = list(result.scalars().all())
    approved_ids: list[uuid.UUID] = []
    now = datetime.now(UTC)
    for m in migrations:
        m.status = "queued"
        m.approved_at = now
        approved_ids.append(m.id)
    await session.flush()
    logger.info("Bulk-approved %d migrations", len(approved_ids))
    return approved_ids


async def detect_drift(session: AsyncSession, settings: Settings) -> list[CorpusMigration]:
    """Scan all corpora for drift. Create pending migrations for drifted ones."""
    # COB's built-in knowledge base follows the engine's embedding on every sync instead.
    stmt = select(Corpus).where(Corpus.is_system.is_(False))
    result = await session.execute(stmt)
    corpora = list(result.scalars().all())

    created: list[CorpusMigration] = []
    for corpus in corpora:
        existing = await get_non_terminal_migration(session, corpus.id)
        if existing:
            continue
        # One corpus the catalog cannot resolve (ValueError from resolve_embedding)
        # must not stop the scan for every other corpus.
        try:
            if is_drifted(corpus, settings):
                migration = await create_migration(session, corpus, settings)
                created.append(migration)
        except (MigrationError, ValueError) as e:
            logger.warning("Drift detection skip for corpus %s: %s", corpus.id, e)
    return created
