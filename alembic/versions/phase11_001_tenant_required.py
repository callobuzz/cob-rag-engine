"""Accounts A1: every corpus belongs to a tenant.

The June tenant layer left `corpora.tenant_id` nullable (FK ON DELETE SET NULL), so a
corpus could sit outside every tenant. The hierarchy is now super admin -> tenant ->
everything the tenant has:

- If any corpus has no tenant, one new tenant named ``Default`` is created and every
  tenant-less corpus moves into it. Its ``max_corpora`` is ``max(10, moved corpora)`` so
  the move never leaves it over its limit. It gets no working ``tk_`` key (empty hash,
  which the middleware refuses); the admin issues one with
  ``POST /api/v1/admin/tenants/{id}/rotate-key``. A fresh install gets no stray tenant.
  A new row is always created, never an existing tenant that happens to be called
  "Default": re-using an operator's own tenant would silently hand it corpora.
- ``usage_daily.tenant_id`` is backfilled from the corpus's tenant, so tenant totals
  never miss rows.
- ``corpora.tenant_id`` and ``usage_daily.tenant_id`` become NOT NULL, and the corpus FK
  becomes ON DELETE RESTRICT: SET NULL is impossible on a NOT NULL column, and CASCADE
  would delete corpus rows in SQL while leaving their Qdrant collections and stored files
  orphaned. A tenant is deleted through the service that removes those first.

Downgrade drops NOT NULL on both columns and restores the SET NULL foreign key. It does
not move corpora back out of Default: which corpora had no tenant is not remembered, and
being in Default is harmless under the old schema.

Revision ID: phase11_001
Revises: phase10_005
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase11_001"
down_revision: str | None = "phase10_005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK_NAME = "fk_corpora_tenant_id"
_OLD_FK_NAME = "corpora_tenant_id_fkey"
_DEFAULT_DESCRIPTION = "Knowledge bases that had no tenant before accounts (migration phase11_001)."


def _tenant_fk_names() -> list[str]:
    """Names of the foreign keys on corpora.tenant_id. phase8_001 created it unnamed, so
    Postgres chose the name; read it rather than hard-code it."""
    inspector = sa.inspect(op.get_bind())
    return [
        fk["name"]
        for fk in inspector.get_foreign_keys("corpora")
        if fk["constrained_columns"] == ["tenant_id"] and fk["name"]
    ]


def upgrade() -> None:
    bind = op.get_bind()
    orphans = bind.execute(
        sa.text("SELECT count(*) FROM corpora WHERE tenant_id IS NULL")
    ).scalar_one()
    if orphans > 0:
        default_id = bind.execute(
            sa.text(
                "INSERT INTO tenants (name, description, max_corpora) "
                "VALUES ('Default', :description, GREATEST(10, :orphans)) RETURNING id"
            ),
            {"description": _DEFAULT_DESCRIPTION, "orphans": orphans},
        ).scalar_one()
        bind.execute(
            sa.text("UPDATE corpora SET tenant_id = :id WHERE tenant_id IS NULL"),
            {"id": default_id},
        )

    op.execute(
        "UPDATE usage_daily u SET tenant_id = c.tenant_id "
        "FROM corpora c WHERE u.corpus_id = c.id AND u.tenant_id IS NULL"
    )

    for name in _tenant_fk_names():
        op.drop_constraint(name, "corpora", type_="foreignkey")
    op.create_foreign_key(
        _FK_NAME, "corpora", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT"
    )

    op.alter_column("corpora", "tenant_id", nullable=False)
    op.alter_column("usage_daily", "tenant_id", nullable=False)


def downgrade() -> None:
    # Data stays where it is: corpora moved into Default remain there (see docstring).
    op.alter_column("usage_daily", "tenant_id", nullable=True)
    op.alter_column("corpora", "tenant_id", nullable=True)
    op.drop_constraint(_FK_NAME, "corpora", type_="foreignkey")
    op.create_foreign_key(
        _OLD_FK_NAME, "corpora", "tenants", ["tenant_id"], ["id"], ondelete="SET NULL"
    )
