"""Phase 9: caller-supplied record ids for text imported via POST /ingest/records.

Revision ID: phase9_003
Revises: phase9_002
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase9_003"
down_revision: str | None = "phase9_002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("external_id", sa.String(500), nullable=True))
    op.create_index(
        "uq_documents_corpus_external_id",
        "documents",
        ["corpus_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_documents_corpus_external_id", table_name="documents")
    op.drop_column("documents", "external_id")
