"""COB, the built-in assistant: the system workspace flags and the sync status row.

Revision ID: phase13_001
Revises: phase12_008
Create Date: 2026-10-04

COB answers from a read-only knowledge base of the engine's own documentation. That knowledge
base lives in a workspace and a knowledge base of its own, which must never be listed to
customers, counted against a plan or changed through the API:

* `tenants.is_system` and `corpora.is_system` (bool, default false). A partial unique index on
  each (`uq_tenants_system`, `uq_corpora_system`) allows at most one row to be true, the same
  way `uq_tenants_operator` does for the operator workspace. The rows themselves are created at
  app start by `cob/system.py`, idempotently, so this migration only adds the columns.
* `cob_status`: a single row (id = 1, check constraint) recording which docs version the system
  knowledge base holds and whether a sync is queued or running. Several app workers start
  together; one UPDATE ... RETURNING on this row decides which of them enqueues the sync.

Downgrade drops the table and the flags; a system workspace row that exists stays as an ordinary
tenant and knowledge base (it can be deleted by hand).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase13_001"
down_revision: str | None = "phase12_008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table, index in (("tenants", "uq_tenants_system"), ("corpora", "uq_corpora_system")):
        op.add_column(
            table,
            sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
        op.create_index(
            index,
            table,
            ["is_system"],
            unique=True,
            postgresql_where=sa.text("is_system"),
        )
    op.create_table(
        "cob_status",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("docs_version", sa.String(64), nullable=True),
        sa.Column("manifest_hash", sa.String(64), nullable=True),
        sa.Column("requested_version", sa.String(64), nullable=True),
        sa.Column("sync_state", sa.String(12), server_default=sa.text("'idle'"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("document_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("eval", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name="ck_cob_status_single_row"),
        sa.CheckConstraint(
            "sync_state in ('idle','queued','running','failed')", name="ck_cob_status_sync_state"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_cob_status"),
    )
    op.execute(sa.text("INSERT INTO cob_status (id) VALUES (1)"))


def downgrade() -> None:
    op.drop_table("cob_status")
    for table, index in (("corpora", "uq_corpora_system"), ("tenants", "uq_tenants_system")):
        op.drop_index(index, table_name=table, postgresql_where=sa.text("is_system"))
        op.drop_column(table, "is_system")
