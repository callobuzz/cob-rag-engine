"""Phase 10: every corpus records the embedding model its collection holds.

`POST /corpora` never set `applied_embedding`, so every corpus created after
phase8_003 had NULL there. The nightly drift check reads NULL as "drifted" and
queued a re-embed onto the very same model for each of them (pending_approval,
from == to). Backfill NULLs from the engine's configured embedding settings (the
ones that built those collections, as phase8_003 did) and cancel the no-op
migrations. Create now sets the column itself.

Revision ID: phase10_002
Revises: phase10_001
Create Date: 2026-09-30
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase10_002"
down_revision: str | None = "phase10_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from callobuzz_rag.core.config import get_settings

    settings = get_settings()
    applied = {
        "provider": settings.embedding_provider,
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dimensions,
    }
    op.execute(
        sa.text(
            "UPDATE corpora SET applied_embedding = CAST(:applied AS jsonb) "
            "WHERE applied_embedding IS NULL"
        ).bindparams(applied=json.dumps(applied))
    )
    op.execute(
        "UPDATE corpus_migrations SET status = 'cancelled', "
        "error = 'No-op: source and target embedding are the same (phase10_002)' "
        "WHERE status = 'pending_approval' "
        "AND from_config->>'provider' = to_config->>'provider' "
        "AND from_config->>'model' = to_config->>'model' "
        "AND from_config->>'dimensions' = to_config->>'dimensions'"
    )


def downgrade() -> None:
    # The backfilled values are correct data, not schema; nothing to undo.
    pass
