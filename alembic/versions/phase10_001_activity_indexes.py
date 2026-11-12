"""Phase 10: indexes for a corpus's last activity.

The corpus list reports each corpus's last activity: the later of its newest
query and its newest ingest job. With (corpus_id, created_at) indexes each of
those is one backward index probe instead of a scan of the corpus's history.

Revision ID: phase10_001
Revises: phase9_005
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "phase10_001"
down_revision: str | None = "phase9_005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_query_log_corpus_id_created_at", "query_log", ["corpus_id", "created_at"])
    op.create_index(
        "ix_ingest_jobs_corpus_id_created_at", "ingest_jobs", ["corpus_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_ingest_jobs_corpus_id_created_at", table_name="ingest_jobs")
    op.drop_index("ix_query_log_corpus_id_created_at", table_name="query_log")
