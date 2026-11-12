"""Phase 6: re-ingestion versioning — IngestJob metadata + DocumentVersion status update.

Revision ID: phase6_001
Revises: phase5_001
Create Date: 2026-03-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase6_001"
down_revision: str | None = "phase5_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # IngestJob: add re-ingestion fields
    op.add_column(
        "ingest_jobs",
        sa.Column("mode", sa.String(20), server_default=sa.text("'auto'"), nullable=False),
    )
    op.add_column(
        "ingest_jobs",
        sa.Column("target_document_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "ingest_jobs",
        sa.Column("review_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "ingest_jobs",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ingest_jobs",
        sa.Column("storage_key", sa.String(500), nullable=True),
    )

    # DocumentVersion: update existing 'completed' statuses to 'active'
    op.execute("UPDATE document_versions SET status = 'active' WHERE status = 'completed'")


def downgrade() -> None:
    # Revert DocumentVersion status values
    op.execute("UPDATE document_versions SET status = 'completed' WHERE status = 'active'")

    op.drop_column("ingest_jobs", "storage_key")
    op.drop_column("ingest_jobs", "expires_at")
    op.drop_column("ingest_jobs", "review_metadata")
    op.drop_column("ingest_jobs", "target_document_id")
    op.drop_column("ingest_jobs", "mode")
