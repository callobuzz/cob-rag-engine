"""Trust and value: the schema of TV2, TV3 and TV4 in one step (phase17_001).

Revision ID: phase17_001
Revises: phase16_002
Create Date: 2026-10-05

One migration for the three epics that run in parallel, so no two lanes ever write a migration
at once (the onboarding steps are TV5's `phase17_002`, WhatsApp is TV6's `phase17_003`).

- `assistant_turns.office_open` (TV4): was the business open when the visitor asked; NULL =
  hours unknown, which is also every turn before this migration (nothing is guessed back).
- `value_settings`, `value_reports` (TV4): the owner's rupee values per workspace and the frozen
  monthly reports.
- `conversations.ref_code` + partial unique index per knowledge base (TV2): the short reference a
  visitor can quote ("SD-4F7K").
- `plan_pricing.effective_at` (TV3): NULL = in force from creation (every existing row); a later
  time = a scheduled price change.
- `billing_accounts.pending_change`, `pending_change_at`, `spend_cap_minor` (TV3): a change that
  applies at the next period, and the owner's monthly spending cap.
- `billing_notices` (TV3): one row per notice e-mail sent, so none goes out twice.
- `support_requests`, `support_messages`, sequence `support_request_number_seq` (TV3): a
  business's requests to the deployer.

Downgrade drops everything in reverse; the sequence goes last.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase17_001"
down_revision: str | None = "phase16_002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")
_GEN = sa.text("gen_random_uuid()")


def _ts(name: str, *, nullable: bool = True, default: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=_NOW if default else None,
        nullable=nullable,
    )


def upgrade() -> None:
    op.add_column("assistant_turns", sa.Column("office_open", sa.Boolean(), nullable=True))

    op.create_table(
        "value_settings",
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("lead_value_minor", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "handoff_value_minor", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("staff_hour_minor", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "minutes_per_answer", sa.SmallInteger(), server_default=sa.text("3"), nullable=False
        ),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("industry_defaults", sa.String(20), nullable=True),
        _ts("updated_at", nullable=False, default=True),
        sa.Column("updated_by", sa.String(320), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_value_settings_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("currency in ('INR','USD')", name="ck_value_settings_currency"),
        sa.CheckConstraint("lead_value_minor >= 0", name="ck_value_settings_lead"),
        sa.CheckConstraint("handoff_value_minor >= 0", name="ck_value_settings_handoff"),
        sa.CheckConstraint("staff_hour_minor >= 0", name="ck_value_settings_staff"),
        sa.CheckConstraint(
            "minutes_per_answer BETWEEN 0 AND 120", name="ck_value_settings_minutes"
        ),
    )

    op.create_table(
        "value_reports",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        _ts("emailed_at"),
        _ts("created_at", nullable=False, default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_value_reports_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "month", name="uq_value_reports_tenant_month"),
    )

    op.add_column("conversations", sa.Column("ref_code", sa.String(8), nullable=True))
    op.create_index(
        "uq_conversations_corpus_ref",
        "conversations",
        ["corpus_id", "ref_code"],
        unique=True,
        postgresql_where=sa.text("ref_code IS NOT NULL"),
    )

    op.add_column("plan_pricing", _ts("effective_at"))
    op.create_index("ix_plan_pricing_plan_effective", "plan_pricing", ["plan_id", "effective_at"])

    op.add_column(
        "billing_accounts", sa.Column("pending_change", postgresql.JSONB(), nullable=True)
    )
    op.add_column("billing_accounts", _ts("pending_change_at"))
    op.add_column("billing_accounts", sa.Column("spend_cap_minor", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        "ck_billing_accounts_spend_cap",
        "billing_accounts",
        "spend_cap_minor IS NULL OR spend_cap_minor >= 0",
    )
    op.create_index(
        "ix_billing_accounts_pending_change_at",
        "billing_accounts",
        ["pending_change_at"],
        postgresql_where=sa.text("pending_change_at IS NOT NULL"),
    )

    op.create_table(
        "billing_notices",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        _ts("sent_at", nullable=False, default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_billing_notices_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "kind", "key", name="uq_billing_notices_tenant_kind_key"),
        sa.CheckConstraint(
            "kind in ('renewal','price_change','price_change_cancelled','pending_change',"
            "'plan_lower')",
            name="ck_billing_notices_kind",
        ),
    )

    op.execute(sa.text("CREATE SEQUENCE support_request_number_seq"))
    op.create_table(
        "support_requests",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column(
            "number",
            sa.Integer(),
            server_default=sa.text("nextval('support_request_number_seq')"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(150), nullable=False),
        sa.Column("status", sa.String(10), server_default=sa.text("'open'"), nullable=False),
        sa.Column("page", sa.String(300), nullable=True),
        sa.Column(
            "context", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("created_by_user_id", _UUID, nullable=True),
        sa.Column("created_by_email", sa.String(320), nullable=False),
        _ts("first_response_at"),
        _ts("last_message_at", nullable=False, default=True),
        _ts("closed_at"),
        _ts("created_at", nullable=False, default=True),
        _ts("updated_at", nullable=False, default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_support_requests_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_support_requests_user_id_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status in ('open','answered','waiting','closed')", name="ck_support_requests_status"
        ),
    )
    op.create_index(
        "ix_support_requests_tenant_created", "support_requests", ["tenant_id", "created_at"]
    )
    op.create_index(
        "ix_support_requests_status_last", "support_requests", ["status", "last_message_at"]
    )

    op.create_table(
        "support_messages",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("request_id", _UUID, nullable=False),
        sa.Column("author_kind", sa.String(10), nullable=False),
        sa.Column("author_email", sa.String(320), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        _ts("created_at", nullable=False, default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["support_requests.id"],
            name="fk_support_messages_request",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "author_kind in ('business','deployer')", name="ck_support_messages_author_kind"
        ),
    )
    op.create_index(
        "ix_support_messages_request_created", "support_messages", ["request_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_support_messages_request_created", table_name="support_messages")
    op.drop_table("support_messages")
    op.drop_index("ix_support_requests_status_last", table_name="support_requests")
    op.drop_index("ix_support_requests_tenant_created", table_name="support_requests")
    op.drop_table("support_requests")
    op.drop_table("billing_notices")

    op.drop_index("ix_billing_accounts_pending_change_at", table_name="billing_accounts")
    op.drop_constraint("ck_billing_accounts_spend_cap", "billing_accounts", type_="check")
    op.drop_column("billing_accounts", "spend_cap_minor")
    op.drop_column("billing_accounts", "pending_change_at")
    op.drop_column("billing_accounts", "pending_change")

    op.drop_index("ix_plan_pricing_plan_effective", table_name="plan_pricing")
    op.drop_column("plan_pricing", "effective_at")

    op.drop_index("uq_conversations_corpus_ref", table_name="conversations")
    op.drop_column("conversations", "ref_code")

    op.drop_table("value_reports")
    op.drop_table("value_settings")
    op.drop_column("assistant_turns", "office_open")
    op.execute(sa.text("DROP SEQUENCE support_request_number_seq"))
