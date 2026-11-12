"""Content Portal: a workspace records what kind it is and which version it reviews.

A post-ingest review workspace (created by `POST /portal/workspaces/from-ingestion`)
used to be recognised only by `review_source` / `document_version_id` keys in its
staged chunks' metadata, which the chunk editor lets anyone edit, and nothing stopped
`finalize-review` from running on an ordinary authoring workspace. Now:

- `kind`: `standard` (authoring) or `post_ingest_review`, set at creation, never edited;
- `reviewed_version_id`: the document version a review was created from (non-editable;
  finalize refuses unless it is still the document's active version);
- `finalized_version_id`: the version a finalized review produced.

Existing review workspaces are backfilled from their staged chunks' metadata (the
version must still exist). The accounts epics' phase11_* revisions chain after this one.

Revision ID: phase10_003
Revises: phase10_002
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase10_003"
down_revision: str | None = "phase10_002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "portal_workspaces",
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'standard'"),
        ),
    )
    op.add_column(
        "portal_workspaces",
        sa.Column(
            "reviewed_version_id",
            sa.UUID(),
            nullable=True,
            comment="Post-ingest review: the document version the review was created from.",
        ),
    )
    op.add_column(
        "portal_workspaces",
        sa.Column(
            "finalized_version_id",
            sa.UUID(),
            nullable=True,
            comment="Post-ingest review: the document version finalizing it produced.",
        ),
    )
    op.create_foreign_key(
        "fk_portal_workspaces_reviewed_version_id",
        "portal_workspaces",
        "document_versions",
        ["reviewed_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_portal_workspaces_finalized_version_id",
        "portal_workspaces",
        "document_versions",
        ["finalized_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_portal_workspaces_reviewed_version_id", "portal_workspaces", ["reviewed_version_id"]
    )

    # Backfill: a workspace whose staged chunks came from a post-ingest review.
    op.execute(
        """
        UPDATE portal_workspaces AS w
        SET kind = 'post_ingest_review',
            reviewed_version_id = src.version_id
        FROM (
            SELECT DISTINCT ON (c.workspace_id)
                   c.workspace_id,
                   v.id AS version_id
            FROM portal_staged_chunks AS c
            LEFT JOIN document_versions AS v
                   ON v.id = CASE
                        WHEN c.metadata ->> 'document_version_id'
                             ~ '^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$'
                        THEN CAST(c.metadata ->> 'document_version_id' AS uuid)
                      END
            WHERE c.metadata ->> 'review_source' = 'post_ingest_review'
            ORDER BY c.workspace_id, v.id NULLS LAST
        ) AS src
        WHERE w.id = src.workspace_id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_portal_workspaces_reviewed_version_id", table_name="portal_workspaces")
    op.drop_constraint(
        "fk_portal_workspaces_finalized_version_id", "portal_workspaces", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_portal_workspaces_reviewed_version_id", "portal_workspaces", type_="foreignkey"
    )
    op.drop_column("portal_workspaces", "finalized_version_id")
    op.drop_column("portal_workspaces", "reviewed_version_id")
    op.drop_column("portal_workspaces", "kind")
