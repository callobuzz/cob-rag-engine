"""An ingest idempotency key is unique within its corpus, not across the engine.

The key was unique across every corpus: one tenant's key clashed with another's, and
the 409 told a caller that some other corpus had used it. Keys are now scoped to
(corpus_id, idempotency_key), and a retry with the same key gets the original job.
Every existing key was globally unique, so it is unique per corpus as well.

Revision ID: phase10_005
Revises: phase10_004
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "phase10_005"
down_revision: str | None = "phase10_004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ingest_jobs_idempotency_key_key", "ingest_jobs", type_="unique")
    op.create_unique_constraint(
        "uq_ingest_jobs_corpus_idempotency_key", "ingest_jobs", ["corpus_id", "idempotency_key"]
    )


def downgrade() -> None:
    # Global uniqueness again: where corpora have since shared a key, only the oldest
    # job keeps it (a key only matters to a client retrying an upload).
    op.execute(
        """
        UPDATE ingest_jobs j SET idempotency_key = NULL
        FROM (
            SELECT id, row_number() OVER (
                PARTITION BY idempotency_key ORDER BY created_at, id
            ) AS rn
            FROM ingest_jobs WHERE idempotency_key IS NOT NULL
        ) ranked
        WHERE j.id = ranked.id AND ranked.rn > 1
        """
    )
    op.drop_constraint("uq_ingest_jobs_corpus_idempotency_key", "ingest_jobs", type_="unique")
    op.create_unique_constraint(
        "ingest_jobs_idempotency_key_key", "ingest_jobs", ["idempotency_key"]
    )
