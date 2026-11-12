"""Queued ingest jobs count against the document limit (accounts A6 fix).

`ingest_jobs.pending_new_documents` (nullable int): how many new documents a job will add
at most, set when the job is queued. The document-limit check counts committed active
documents plus this number for the tenant's in-flight jobs, so rapid or concurrent
submissions cannot overshoot the limit.

Backfill: jobs already in flight get a conservative estimate (replace/version of a named
document 0, a records import its record count, any other upload 1). Finished jobs stay
NULL; they are never counted.

Revision ID: phase11_007
Revises: phase11_006
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase11_007"
down_revision: str | None = "phase11_006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ingest_jobs", sa.Column("pending_new_documents", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE ingest_jobs SET pending_new_documents = CASE
            WHEN mode IN ('replace', 'version') AND target_document_id IS NOT NULL THEN 0
            WHEN mode = 'records' THEN COALESCE(total_documents, 0)
            ELSE 1
        END
        WHERE status IN (
            'queued', 'uploading', 'parsing', 'chunking', 'enriching', 'embedding', 'storing'
        )
        """
    )


def downgrade() -> None:
    op.drop_column("ingest_jobs", "pending_new_documents")
