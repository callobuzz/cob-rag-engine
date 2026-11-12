"""Guided assistant setup sessions (conversation layer C8).

One row per walk through the setup chat: what the crawl proposed, what the owner confirmed and
answered, the config generated from it with per-item provenance, and the golden set / golden run
(C7) that checked it.
CASCADE from the knowledge base; the golden set and run are kept by their own tables and only
unlinked (SET NULL) when deleted there.

Revision ID: phase12_006
Revises: phase12_005
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase12_006"
down_revision: str | None = "phase12_005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_setup_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "corpus_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("corpora.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'started'")),
        sa.Column("business_type", sa.String(32), nullable=True),
        sa.Column("website_url", sa.String(2048), nullable=True),
        sa.Column("crawl_job_id", sa.String(64), nullable=True),
        sa.Column("proposal", postgresql.JSONB(), nullable=True),
        sa.Column("profile", postgresql.JSONB(), nullable=True),
        sa.Column(
            "answers", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("generated_config", postgresql.JSONB(), nullable=True),
        sa.Column(
            "provenance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "golden_set_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("golden_sets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "eval_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("eval_run.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(
            "status IN ('started','proposed','interviewing','generated','tested','applied',"
            "'abandoned')",
            name="ck_assistant_setup_sessions_status",
        ),
    )
    op.create_index(
        "ix_assistant_setup_sessions_corpus_created",
        "assistant_setup_sessions",
        ["corpus_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_setup_sessions_corpus_created", "assistant_setup_sessions")
    op.drop_table("assistant_setup_sessions")
