"""MigrationContext — seam for dual-write during active migrations.

The ingestion pipeline and memory extraction call get_migration_context()
at the embed step to discover if a dual-write is needed. The orchestrator
stays migration-unaware beyond this hook.

Every write to a corpus's points honours the window, not only adding them:
whatever retires a version (re-ingest, record updates, version restore, merge,
delete) removes its points from the new collection too, through
get_dual_write_collection(), and whatever adds a version's points writes them
there as well. A point the migration copied before its version was retired
would otherwise survive in the new collection and reappear at cutover.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.db.models.corpus_migration import CorpusMigration

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.embeddings.base import EmbeddingProvider

logger = logging.getLogger(__name__)


@dataclass
class DualWriteTarget:
    """Secondary embed target during a migration window."""

    embedder: "EmbeddingProvider"
    collection: str
    model: str


async def _open_migration(session: AsyncSession, corpus_id: uuid.UUID) -> CorpusMigration | None:
    """The corpus's re_embed migration whose dual-write window is open, if any."""
    stmt = select(CorpusMigration).where(
        CorpusMigration.corpus_id == corpus_id,
        CorpusMigration.strategy == "re_embed",
        CorpusMigration.status.in_(("running", "verifying")),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_dual_write_collection(session: AsyncSession, corpus_id: uuid.UUID) -> str | None:
    """The migration's new collection while a dual-write window is open, else None.

    What removing points needs: a version retired during a migration has to leave the
    new collection too, or it comes back at cutover. No embedder is built.
    """
    migration = await _open_migration(session, corpus_id)
    return migration.new_collection if migration is not None else None


async def get_migration_context(
    session: AsyncSession,
    corpus_id: uuid.UUID,
    settings: "Settings",
) -> DualWriteTarget | None:
    """Check for an active re_embed migration on this corpus.

    Returns the secondary (embedder, collection, model) if a dual-write window
    is open (migration status is running or verifying). Returns None otherwise.
    """
    migration = await _open_migration(session, corpus_id)
    if migration is None:
        return None

    from callobuzz_rag.core.config_resolver import ResolvedEmbeddingConfig
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.embeddings.factory import corpus_embedding_key, create_embedding_provider

    to_config = migration.to_config
    corpus = await session.get(Corpus, corpus_id)
    resolved = ResolvedEmbeddingConfig(
        provider=to_config["provider"],
        model=to_config["model"],
        dimensions=to_config["dimensions"],
        api_key=(corpus_embedding_key(corpus, settings, to_config["model"]) if corpus else None),
    )
    # Uncached: ingestion runs in a worker task's own event loop; the caller closes it.
    embedder = create_embedding_provider(settings, resolved=resolved, cache=False)
    return DualWriteTarget(
        embedder=embedder,
        collection=migration.new_collection,
        model=to_config["model"],
    )
