"""Provider balances: what the super admin knows about each LLM provider's money.

Revision ID: phase14_001
Revises: phase13_003
Create Date: 2026-10-04

One row per provider the super admin configured on the Costs page: OpenRouter, OpenAI and
Anthropic at most once each (a partial unique index), and any number of `manual` rows (a prepaid
balance typed in for a provider that has no API: Groq, Gemini...). A row holds the manual balance
and the date it was true, an alert threshold, the alert state (so one e-mail is sent per
crossing, not one every check) and, optionally, the provider's admin key, Fernet-encrypted. The
key lives here and not in the `engine_settings` JSON because that JSON is returned whole by
`GET /admin/settings`.

Downgrade drops the table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase14_001"
down_revision: str | None = "phase13_003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_balance_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("admin_key_encrypted", sa.Text(), nullable=True),
        sa.Column("manual_balance_usd_micro", sa.BigInteger(), nullable=True),
        sa.Column("manual_balance_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manual_note", sa.Text(), nullable=True),
        sa.Column("low_threshold_usd_micro", sa.BigInteger(), nullable=True),
        sa.Column("alert_state", sa.String(8), server_default=sa.text("'ok'"), nullable=False),
        sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(320), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "provider in ('openrouter','openai','anthropic','manual')",
            name="ck_provider_balance_settings_provider",
        ),
        sa.CheckConstraint(
            "manual_balance_usd_micro IS NULL OR manual_balance_usd_micro >= 0",
            name="ck_provider_balance_settings_balance",
        ),
        sa.CheckConstraint(
            "(manual_balance_usd_micro IS NULL) = (manual_balance_at IS NULL)",
            name="ck_provider_balance_settings_balance_pair",
        ),
        sa.CheckConstraint(
            "manual_note IS NULL OR char_length(manual_note) <= 500",
            name="ck_provider_balance_settings_note",
        ),
        sa.CheckConstraint(
            "low_threshold_usd_micro IS NULL OR low_threshold_usd_micro >= 0",
            name="ck_provider_balance_settings_threshold",
        ),
        sa.CheckConstraint(
            "alert_state in ('ok','low','empty')",
            name="ck_provider_balance_settings_alert_state",
        ),
    )
    op.create_index(
        "uq_provider_balance_settings_provider",
        "provider_balance_settings",
        ["provider"],
        unique=True,
        postgresql_where=sa.text("provider <> 'manual'"),
    )


def downgrade() -> None:
    op.drop_index("uq_provider_balance_settings_provider", table_name="provider_balance_settings")
    op.drop_table("provider_balance_settings")
