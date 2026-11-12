"""Value report: who the monthly e-mail already went to (phase17_006).

Revision ID: phase17_006
Revises: phase17_005
Create Date: 2026-10-06

- `value_reports.emailed_to`: the addresses the report was queued to, so a queue failure for one
  address is retried for that address only and nobody gets the same month twice.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase17_006"
down_revision: str | None = "phase17_005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "value_reports",
        sa.Column(
            "emailed_to",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    # a report already stamped `emailed_at` has no per-address record; it is never sent again


def downgrade() -> None:
    op.drop_column("value_reports", "emailed_to")
