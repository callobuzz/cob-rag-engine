"""At most one active version per document.

Ingest (re-ingest into an existing document), version restore and the Content Portal's
review finalize each retire the active version and activate another. Only finalize
locked anything, so two of them racing on one document could leave two ACTIVE
versions, both searchable. Ingest and restore now lock the document row, as finalize
does, and this partial unique index makes a second active version impossible.

Existing duplicates, if any, are resolved first: the newest active version (by
created_at) stays active, the others become inactive. Their points stay in Qdrant, so
the upgrade logs each affected corpus; `python -m callobuzz_rag.retrieval.reindex
--rebuild --corpus <id>` rewrites a collection from the active versions only.

Revision ID: phase10_004
Revises: phase10_003
Create Date: 2026-09-30
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase10_004"
down_revision: str | None = "phase10_003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    demoted = (
        op.get_bind()
        .execute(
            sa.text(
                """
                UPDATE document_versions v SET status = 'inactive'
                FROM (
                    SELECT id, row_number() OVER (
                        PARTITION BY document_id ORDER BY created_at DESC, id DESC
                    ) AS rn
                    FROM document_versions WHERE status = 'active'
                ) ranked
                WHERE v.id = ranked.id AND ranked.rn > 1
                RETURNING v.id, v.document_id
                """
            )
        )
        .all()
    )
    if demoted:
        corpora = (
            op.get_bind()
            .execute(
                sa.text("SELECT DISTINCT corpus_id FROM documents WHERE id = ANY(:ids)"),
                {"ids": [row.document_id for row in demoted]},
            )
            .scalars()
            .all()
        )
        logger.warning(
            "phase10_004: %d duplicate active version(s) made inactive; their points are "
            "still in Qdrant. Rebuild these corpora's collections with "
            "`python -m callobuzz_rag.retrieval.reindex --apply --rebuild --corpus <id>`: %s",
            len(demoted),
            ", ".join(str(c) for c in corpora),
        )
    op.create_index(
        "uq_document_versions_one_active",
        "document_versions",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_document_versions_one_active", table_name="document_versions")
