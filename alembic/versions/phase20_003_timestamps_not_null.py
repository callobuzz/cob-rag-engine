"""Schema drift: the billing, WhatsApp and service-order timestamps become NOT NULL.

Revision ID: phase20_003
Revises: phase20_002
Create Date: 2026-10-16

`alembic check` reported `created_at` / `updated_at` (and `channel_events.received_at`) nullable
in the database but NOT NULL in the models, for the tables added after phase12_008 fixed the same
drift for the older ones: `billing_accounts` (phase14_002), `plan_pricing` (phase14_002),
`channel_connections`, `channel_threads`, `channel_events` (phase17_003) and `service_orders`
(phase19_004). Every one has a server default of now(), so a NULL could only come from a
hand-written insert. As in phase12_008 the safer side wins: NULLs are backfilled (`updated_at`
from `created_at`) and the column becomes NOT NULL.

Downgrade makes the columns nullable again; the backfilled values stay.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase20_003"
down_revision: str | None = "phase20_002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column): timestamptz DEFAULT now(), nullable in the database, NOT NULL in the models.
_TIMESTAMPS: list[tuple[str, str]] = [
    ("billing_accounts", "created_at"),
    ("billing_accounts", "updated_at"),
    ("channel_connections", "created_at"),
    ("channel_connections", "updated_at"),
    ("channel_events", "received_at"),
    ("channel_threads", "created_at"),
    ("channel_threads", "updated_at"),
    ("plan_pricing", "created_at"),
    ("service_orders", "created_at"),
    ("service_orders", "updated_at"),
]


def upgrade() -> None:
    tables_with_created = {t for t, c in _TIMESTAMPS if c == "created_at"}
    for table, column in _TIMESTAMPS:
        if column == "updated_at" and table in tables_with_created:
            op.execute(f'UPDATE "{table}" SET updated_at = created_at WHERE updated_at IS NULL')
        op.execute(f'UPDATE "{table}" SET "{column}" = now() WHERE "{column}" IS NULL')
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            existing_server_default=sa.text("now()"),
        )


def downgrade() -> None:
    for table, column in _TIMESTAMPS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
            existing_server_default=sa.text("now()"),
        )
