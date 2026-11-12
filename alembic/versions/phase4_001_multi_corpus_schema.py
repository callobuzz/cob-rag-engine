"""Phase 4: multi-corpus schema changes.

Adds per-corpus auth, collection routing, LLM config, ingest job scoping,
and all Phase 3 tables/columns that were added since the initial migration.

Revision ID: phase4_001
Revises: d93d4cf5242e
Create Date: 2026-03-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase4_001"
down_revision: str | None = "d93d4cf5242e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ======================================================================
    # Phase 3 new tables
    # ======================================================================

    op.create_table(
        "eval_run",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("triggered_by", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("config_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "gap_report",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("query_count", sa.Integer(), nullable=False),
        sa.Column("cluster_count", sa.Integer(), nullable=False),
        sa.Column("clusters", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("config_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "golden_pair",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("corpus_id", sa.UUID(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("expected_answer", sa.Text(), nullable=False),
        sa.Column(
            "expected_chunk_ids",
            postgresql.ARRAY(sa.UUID()),
            nullable=True,
        ),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["corpus_id"], ["corpora.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "optimization_runs",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'running'"),
            nullable=False,
        ),
        sa.Column("n_trials", sa.Integer(), nullable=False),
        sa.Column("pareto_configs", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("best_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("search_space", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "webhook_config",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column(
            "events",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("secret", sa.String(length=256), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ======================================================================
    # Phase 3 column additions to existing tables
    # ======================================================================

    # query_log: add corpus_id, session_id, trace_id
    op.add_column(
        "query_log",
        sa.Column("corpus_id", sa.UUID(), nullable=True),
    )
    op.create_index("ix_query_log_corpus_id", "query_log", ["corpus_id"])
    op.add_column(
        "query_log",
        sa.Column("session_id", sa.String(length=100), nullable=True),
    )
    op.create_index("ix_query_log_session_id", "query_log", ["session_id"])
    op.add_column(
        "query_log",
        sa.Column("trace_id", sa.String(length=100), nullable=True),
    )
    op.create_index("ix_query_log_trace_id", "query_log", ["trace_id"])

    # feedback: add feedback_type, session_id, trace_id; update query_log FK ondelete
    op.add_column(
        "feedback",
        sa.Column(
            "feedback_type",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'explicit'"),
        ),
    )
    op.add_column(
        "feedback",
        sa.Column("session_id", sa.String(length=100), nullable=True),
    )
    op.create_index("ix_feedback_session_id", "feedback", ["session_id"])
    op.add_column(
        "feedback",
        sa.Column("trace_id", sa.String(length=100), nullable=True),
    )

    # ======================================================================
    # Phase 4 column additions
    # ======================================================================

    # corpora: add Phase 4 isolation columns
    op.add_column(
        "corpora",
        sa.Column(
            "api_key_hash",
            sa.String(length=255),
            server_default=sa.text("''"),
            nullable=False,
        ),
    )
    op.add_column(
        "corpora",
        sa.Column(
            "qdrant_collection",
            sa.String(length=255),
            server_default=sa.text("'chunks'"),
            nullable=False,
        ),
    )
    op.add_column(
        "corpora",
        sa.Column(
            "llm_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "corpora",
        sa.Column(
            "prompt_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # ingest_jobs: add corpus_id FK
    op.add_column(
        "ingest_jobs",
        sa.Column("corpus_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_ingest_jobs_corpus",
        "ingest_jobs",
        "corpora",
        ["corpus_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_ingest_jobs_corpus_id", "ingest_jobs", ["corpus_id"])


def downgrade() -> None:
    # --- Phase 4 rollback ---
    op.drop_index("ix_ingest_jobs_corpus_id", table_name="ingest_jobs")
    op.drop_constraint("fk_ingest_jobs_corpus", "ingest_jobs", type_="foreignkey")
    op.drop_column("ingest_jobs", "corpus_id")
    op.drop_column("corpora", "prompt_config")
    op.drop_column("corpora", "llm_config")
    op.drop_column("corpora", "qdrant_collection")
    op.drop_column("corpora", "api_key_hash")

    # --- Phase 3 column rollback ---
    op.drop_column("feedback", "trace_id")
    op.drop_index("ix_feedback_session_id", table_name="feedback")
    op.drop_column("feedback", "session_id")
    op.drop_column("feedback", "feedback_type")
    op.drop_index("ix_query_log_trace_id", table_name="query_log")
    op.drop_column("query_log", "trace_id")
    op.drop_index("ix_query_log_session_id", table_name="query_log")
    op.drop_column("query_log", "session_id")
    op.drop_index("ix_query_log_corpus_id", table_name="query_log")
    op.drop_column("query_log", "corpus_id")

    # --- Phase 3 table rollback ---
    op.drop_table("webhook_config")
    op.drop_table("optimization_runs")
    op.drop_table("golden_pair")
    op.drop_table("gap_report")
    op.drop_table("eval_run")
