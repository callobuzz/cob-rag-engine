"""Phase 9: document metadata in Postgres; cascades the models declared but the DB lacked.

- documents.doc_metadata: the metadata given at ingest, so vectors rebuilt from Postgres
  (embedding migration, version restore, reindex --rebuild) keep their filters.
- feedback.query_log_id: the model says ON DELETE CASCADE; the initial migration
  created the constraint without it.
- query_log.corpus_id: had no foreign key at all, so a deleted corpus left its users'
  questions behind. Rows already orphaned that way are removed before the constraint
  is added — they belong to corpora that were deleted.

Revision ID: phase9_004
Revises: phase9_003
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase9_004"
down_revision: str | None = "phase9_003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("doc_metadata", postgresql.JSONB(), nullable=True))

    op.drop_constraint("feedback_query_log_id_fkey", "feedback", type_="foreignkey")
    op.create_foreign_key(
        "feedback_query_log_id_fkey",
        "feedback",
        "query_log",
        ["query_log_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.execute(
        "DELETE FROM query_log WHERE corpus_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM corpora WHERE corpora.id = query_log.corpus_id)"
    )
    op.create_foreign_key(
        "query_log_corpus_id_fkey",
        "query_log",
        "corpora",
        ["corpus_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("query_log_corpus_id_fkey", "query_log", type_="foreignkey")
    op.drop_constraint("feedback_query_log_id_fkey", "feedback", type_="foreignkey")
    op.create_foreign_key(
        "feedback_query_log_id_fkey", "feedback", "query_log", ["query_log_id"], ["id"]
    )
    op.drop_column("documents", "doc_metadata")
