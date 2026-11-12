"""Plan setting: count cached answers.

`plans.count_cached_answers` (bool, default true): whether an answer served from the query
cache counts toward the tenant's monthly question cap and counter (and is refused at the cap).
false: cached answers are free. Existing plans get true, which is what the engine did for the
cap check already (the counter had skipped them).

Revision ID: phase11_011
Revises: phase11_010
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase11_011"
down_revision: str | None = "phase11_010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column(
            "count_cached_answers", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
    )


def downgrade() -> None:
    op.drop_column("plans", "count_cached_answers")
