"""Phase 8: Embedding model configuration & migration infrastructure.

Adds per-corpus embedding_config + applied_embedding JSONB columns,
memory staging columns, and the corpus_migrations table for blue-green
re-embedding jobs.

Revision ID: phase8_003
Revises: phase8_002
Create Date: 2026-06-11
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase8_003"
down_revision: str | None = "phase8_002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Corpus: embedding config columns ---
    op.add_column(
        "corpora",
        sa.Column(
            "embedding_config",
            postgresql.JSONB(),
            nullable=True,
            comment="Per-corpus embedding overrides: provider, model, dimensions, api_key_encrypted",
        ),
    )
    op.add_column(
        "corpora",
        sa.Column(
            "applied_embedding",
            postgresql.JSONB(),
            nullable=True,
            comment="What the live collection actually contains: provider, model, dimensions",
        ),
    )

    # Backfill applied_embedding for existing corpora from the engine's CONFIGURED
    # embedding settings — the ones that produced the vectors already stored. This
    # used to hard-code {"provider": "local", ...}; on a deployment running TEI the
    # drift check then saw "local" != "tei" and queued a re-embed of every corpus
    # onto the same model.
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

    # --- Memory: staging columns for blue-green migration ---
    op.add_column(
        "memories",
        sa.Column("embedding_staged", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "memories",
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
    )

    # Backfill embedding_model for existing memories that have embeddings.
    op.execute(
        sa.text(
            "UPDATE memories SET embedding_model = :model "
            "WHERE embedding IS NOT NULL AND embedding_model IS NULL"
        ).bindparams(model=settings.embedding_model)
    )

    # --- corpus_migrations table ---
    op.create_table(
        "corpus_migrations",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("corpus_id", sa.UUID(), nullable=False),
        sa.Column("strategy", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("from_config", postgresql.JSONB(), nullable=False),
        sa.Column("to_config", postgresql.JSONB(), nullable=False),
        sa.Column("old_collection", sa.String(length=255), nullable=False),
        sa.Column("new_collection", sa.String(length=255), nullable=False),
        sa.Column("chunks_total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("chunks_done", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("memories_total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("memories_done", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prune_after", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["corpus_id"], ["corpora.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_corpus_migrations_corpus_id", "corpus_migrations", ["corpus_id"])
    op.create_index("ix_corpus_migrations_status", "corpus_migrations", ["status"])
    op.create_index(
        "ix_corpus_migrations_non_terminal",
        "corpus_migrations",
        ["corpus_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending_approval', 'queued', 'running', 'verifying', 'rolling_back')"
        ),
    )


def downgrade() -> None:
    op.drop_table("corpus_migrations")
    op.drop_column("memories", "embedding_model")
    op.drop_column("memories", "embedding_staged")
    op.drop_column("corpora", "applied_embedding")
    op.drop_column("corpora", "embedding_config")
