"""Own-key flag, feature and service add-ons, service orders (P5).

Revision ID: phase19_004
Revises: phase19_003
Create Date: 2026-10-08

- `plans.byok_allowed` (default true): every plan that exists today keeps letting its businesses
  use their own AI key, so an upgrade changes nothing for an existing install. The seeded Free
  and Standard plans say no (`billing/seed_saas.py`).
- `add_on_catalogue` / `add_on_grants`: two new kinds, `feature_unlock` (`feature = 'byok'`, a
  month or for good, may renew) and `service` (a one-time price, optional months of support,
  may renew), plus the columns they need. Existing add-ons are `sold_to_owners = false`, so
  nothing new appears in a business owner's shop.
- `service_orders`: one row per service grant, tracked in the Support inbox.

The kind and field checks are dropped and rebuilt around the two new kinds. Downgrade refuses
while a `feature_unlock` or `service` add-on or grant exists, because they could not be kept.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase19_004"
down_revision: str | None = "phase19_003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_OLD_KINDS = "kind in ('credit_pack','extra_limit','question_pack')"
_NEW_KINDS = "kind in ('credit_pack','extra_limit','question_pack','feature_unlock','service')"

_OLD_FIELDS = (
    "(kind = 'credit_pack' AND credit_minor IS NOT NULL AND credit_minor > 0"
    " AND limit_field IS NULL AND limit_delta IS NULL AND questions IS NULL)"
    " OR (kind = 'extra_limit' AND credit_minor IS NULL AND limit_field IS NOT NULL"
    " AND limit_delta IS NOT NULL AND questions IS NULL)"
    " OR (kind = 'question_pack' AND credit_minor IS NULL AND limit_field IS NULL"
    " AND limit_delta IS NULL AND questions IS NOT NULL)"
)
_NEW_FIELDS = (
    "(kind = 'credit_pack' AND credit_minor IS NOT NULL AND credit_minor > 0"
    " AND limit_field IS NULL AND limit_delta IS NULL AND questions IS NULL AND feature IS NULL)"
    " OR (kind = 'extra_limit' AND credit_minor IS NULL AND limit_field IS NOT NULL"
    " AND limit_delta IS NOT NULL AND questions IS NULL AND feature IS NULL)"
    " OR (kind = 'question_pack' AND credit_minor IS NULL AND limit_field IS NULL"
    " AND limit_delta IS NULL AND questions IS NOT NULL AND feature IS NULL)"
    " OR (kind = 'feature_unlock' AND credit_minor IS NULL AND limit_field IS NULL"
    " AND limit_delta IS NULL AND questions IS NULL AND feature IS NOT NULL)"
    " OR (kind = 'service' AND credit_minor IS NULL AND limit_field IS NULL"
    " AND limit_delta IS NULL AND questions IS NULL AND feature IS NULL)"
)
_RENEWS = "NOT renews OR kind = 'service' OR (kind = 'feature_unlock' AND duration = 'period')"


def _swap(table: str, name: str, new_text: str) -> None:
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, new_text)


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("byok_allowed", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )

    for table in ("add_on_catalogue", "add_on_grants"):
        op.add_column(table, sa.Column("feature", sa.String(16), nullable=True))
        op.add_column(
            table,
            sa.Column("renews", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        )
        op.add_column(table, sa.Column("includes_support_months", sa.SmallInteger(), nullable=True))
        op.add_column(table, sa.Column("sac_code", sa.String(6), nullable=True))
    op.add_column(
        "add_on_catalogue",
        sa.Column("sold_to_owners", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "add_on_catalogue",
        sa.Column("placeholder", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "add_on_grants", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("add_on_grants", sa.Column("renewed_through", sa.Date(), nullable=True))

    _swap("add_on_catalogue", "ck_add_on_kind", _NEW_KINDS)
    _swap("add_on_catalogue", "ck_add_on_fields_match_kind", _NEW_FIELDS)
    _swap(
        "add_on_catalogue",
        "ck_add_on_duration_kind",
        "(kind in ('extra_limit','feature_unlock')) = (duration IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_add_on_feature", "add_on_catalogue", "feature IS NULL OR feature in ('byok')"
    )
    op.create_check_constraint("ck_add_on_renews", "add_on_catalogue", _RENEWS)
    op.create_check_constraint(
        "ck_add_on_support_months",
        "add_on_catalogue",
        "includes_support_months IS NULL OR "
        "(kind = 'service' AND includes_support_months BETWEEN 1 AND 36)",
    )
    op.create_check_constraint(
        "ck_add_on_sac_code", "add_on_catalogue", "sac_code IS NULL OR sac_code ~ '^99[0-9]{4}$'"
    )
    _swap("add_on_grants", "ck_add_on_grant_kind", _NEW_KINDS)
    _swap("add_on_grants", "ck_add_on_grant_fields_match_kind", _NEW_FIELDS)
    op.create_index(
        "ix_add_on_grants_renewing",
        "add_on_grants",
        ["ends_at"],
        postgresql_where=sa.text("renews AND revoked_at IS NULL AND cancelled_at IS NULL"),
    )

    op.create_table(
        "service_orders",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("grant_id", _UUID, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(12), server_default=sa.text("'requested'"), nullable=False),
        sa.Column("owner_note", sa.Text(), nullable=True),
        sa.Column("public_note", sa.Text(), nullable=True),
        sa.Column("internal_note", sa.Text(), nullable=True),
        sa.Column("support_request_id", _UUID, nullable=True),
        sa.Column("includes_support_months", sa.SmallInteger(), nullable=True),
        sa.Column("support_until", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_service_orders"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_service_orders_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["grant_id"],
            ["add_on_grants.id"],
            ondelete="RESTRICT",
            name="fk_service_orders_grant_id",
        ),
        sa.ForeignKeyConstraint(
            ["support_request_id"],
            ["support_requests.id"],
            ondelete="SET NULL",
            name="fk_service_orders_support_request",
        ),
        sa.UniqueConstraint("grant_id", name="uq_service_orders_grant_id"),
        sa.CheckConstraint(
            "status in ('requested','in_progress','done','cancelled')",
            name="ck_service_orders_status",
        ),
        sa.CheckConstraint(
            "includes_support_months IS NULL OR includes_support_months BETWEEN 1 AND 36",
            name="ck_service_orders_support_months",
        ),
        sa.CheckConstraint(
            "(owner_note IS NULL OR char_length(owner_note) <= 1000)"
            " AND (public_note IS NULL OR char_length(public_note) <= 1000)",
            name="ck_service_orders_notes",
        ),
    )
    op.create_index(
        "ix_service_orders_tenant_created", "service_orders", ["tenant_id", "created_at"]
    )
    op.create_index("ix_service_orders_status_created", "service_orders", ["status", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    found = bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM add_on_catalogue WHERE kind IN ('feature_unlock','service'))"
            " + (SELECT count(*) FROM add_on_grants WHERE kind IN ('feature_unlock','service'))"
        )
    ).scalar_one()
    if found:
        raise RuntimeError(
            f"{found} feature-unlock or service add-on(s) or grant(s) exist; they cannot be "
            "kept after downgrading. Remove them first."
        )
    op.drop_table("service_orders")
    op.drop_index("ix_add_on_grants_renewing", table_name="add_on_grants")
    _swap("add_on_grants", "ck_add_on_grant_fields_match_kind", _OLD_FIELDS)
    _swap("add_on_grants", "ck_add_on_grant_kind", _OLD_KINDS)
    op.drop_constraint("ck_add_on_sac_code", "add_on_catalogue", type_="check")
    op.drop_constraint("ck_add_on_support_months", "add_on_catalogue", type_="check")
    op.drop_constraint("ck_add_on_renews", "add_on_catalogue", type_="check")
    op.drop_constraint("ck_add_on_feature", "add_on_catalogue", type_="check")
    _swap(
        "add_on_catalogue",
        "ck_add_on_duration_kind",
        "(kind = 'extra_limit') = (duration IS NOT NULL)",
    )
    _swap("add_on_catalogue", "ck_add_on_fields_match_kind", _OLD_FIELDS)
    _swap("add_on_catalogue", "ck_add_on_kind", _OLD_KINDS)
    op.drop_column("add_on_grants", "renewed_through")
    op.drop_column("add_on_grants", "cancelled_at")
    op.drop_column("add_on_catalogue", "placeholder")
    op.drop_column("add_on_catalogue", "sold_to_owners")
    for table in ("add_on_grants", "add_on_catalogue"):
        op.drop_column(table, "sac_code")
        op.drop_column(table, "includes_support_months")
        op.drop_column(table, "renews")
        op.drop_column(table, "feature")
    op.drop_column("plans", "byok_allowed")
