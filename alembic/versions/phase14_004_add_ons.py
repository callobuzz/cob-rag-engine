"""Billing: the add-on catalogue and the grants made from it.

Revision ID: phase14_004
Revises: phase14_003
Create Date: 2026-10-04

`add_on_catalogue` is what the super admin sells on top of a plan: credit packs, extra limits and
question packs. `add_on_grants` is one add-on applied to one workspace; it copies the add-on's
fields so editing the catalogue never rewrites what a customer was given, and it links the
ledger entry (credit pack, or an add-on paid from the balance) when there is one. Effective limits
are the plan's, the account's overrides, plus the active grants. A grant belongs to its workspace
(deleted with it); an add-on that was ever granted cannot be deleted (RESTRICT).

Downgrade drops both tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase14_004"
down_revision: str | None = "phase14_003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")

_LIMIT_FIELDS = (
    "limit_field in ('max_knowledge_bases','max_documents','max_widget_keys','max_upload_mb',"
    "'max_document_tokens')"
)
_KIND_FIELDS = (
    "(kind = 'credit_pack' AND credit_minor IS NOT NULL AND credit_minor > 0"
    " AND limit_field IS NULL AND limit_delta IS NULL AND questions IS NULL)"
    " OR (kind = 'extra_limit' AND credit_minor IS NULL AND limit_field IS NOT NULL"
    " AND limit_delta IS NOT NULL AND questions IS NULL)"
    " OR (kind = 'question_pack' AND credit_minor IS NULL AND limit_field IS NULL"
    " AND limit_delta IS NULL AND questions IS NOT NULL)"
)


def upgrade() -> None:
    op.create_table(
        "add_on_catalogue",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("price_minor", sa.BigInteger(), nullable=False),
        sa.Column("credit_minor", sa.BigInteger(), nullable=True),
        sa.Column("bonus_pct", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("expiry_days", sa.Integer(), nullable=True),
        sa.Column("limit_field", sa.String(32), nullable=True),
        sa.Column("limit_delta", sa.Integer(), nullable=True),
        sa.Column("questions", sa.Integer(), nullable=True),
        sa.Column("duration", sa.String(10), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_add_on_catalogue_name"),
        sa.CheckConstraint(
            "kind in ('credit_pack','extra_limit','question_pack')", name="ck_add_on_kind"
        ),
        sa.CheckConstraint("currency in ('INR','USD')", name="ck_add_on_currency"),
        sa.CheckConstraint("price_minor >= 0", name="ck_add_on_price"),
        sa.CheckConstraint("bonus_pct BETWEEN 0 AND 100", name="ck_add_on_bonus"),
        sa.CheckConstraint(
            "expiry_days IS NULL OR expiry_days BETWEEN 1 AND 3650", name="ck_add_on_expiry"
        ),
        sa.CheckConstraint(f"limit_field IS NULL OR {_LIMIT_FIELDS}", name="ck_add_on_limit_field"),
        sa.CheckConstraint("limit_delta IS NULL OR limit_delta >= 1", name="ck_add_on_limit_delta"),
        sa.CheckConstraint("questions IS NULL OR questions >= 1", name="ck_add_on_questions"),
        sa.CheckConstraint(
            "duration IS NULL OR duration in ('period','permanent')", name="ck_add_on_duration"
        ),
        sa.CheckConstraint(_KIND_FIELDS, name="ck_add_on_fields_match_kind"),
        sa.CheckConstraint(
            "(kind = 'extra_limit') = (duration IS NOT NULL)", name="ck_add_on_duration_kind"
        ),
        sa.CheckConstraint(
            "(kind = 'credit_pack') OR (bonus_pct = 0 AND expiry_days IS NULL)",
            name="ck_add_on_credit_only_fields",
        ),
    )
    op.create_table(
        "add_on_grants",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("add_on_id", _UUID, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("price_minor", sa.BigInteger(), nullable=False),
        sa.Column("credit_minor", sa.BigInteger(), nullable=True),
        sa.Column("bonus_pct", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("limit_field", sa.String(32), nullable=True),
        sa.Column("limit_delta", sa.Integer(), nullable=True),
        sa.Column("questions", sa.Integer(), nullable=True),
        sa.Column("payment", sa.String(10), nullable=False),
        sa.Column("entry_id", _UUID, nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(320), nullable=True),
        sa.Column("revoke_memo", sa.Text(), nullable=True),
        sa.Column("granted_by", sa.String(320), nullable=False),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_add_on_grants_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["add_on_id"],
            ["add_on_catalogue.id"],
            name="fk_add_on_grants_add_on_id_add_ons",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["wallet_ledger.id"],
            name="fk_add_on_grants_entry_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "kind in ('credit_pack','extra_limit','question_pack')", name="ck_add_on_grant_kind"
        ),
        sa.CheckConstraint(
            "payment in ('wallet','external','free')", name="ck_add_on_grant_payment"
        ),
        sa.CheckConstraint(_KIND_FIELDS, name="ck_add_on_grant_fields_match_kind"),
    )
    op.create_index(
        "ix_add_on_grants_tenant_active",
        "add_on_grants",
        ["tenant_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index("ix_add_on_grants_add_on", "add_on_grants", ["add_on_id"])


def downgrade() -> None:
    op.drop_index("ix_add_on_grants_add_on", table_name="add_on_grants")
    op.drop_index("ix_add_on_grants_tenant_active", table_name="add_on_grants")
    op.drop_table("add_on_grants")
    op.drop_table("add_on_catalogue")
