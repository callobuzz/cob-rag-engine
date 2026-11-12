"""Lead withdrawal hash and consent erasure date (conversation layer C4 review fixes).

- `leads.withdraw_hash`: sha256 of the secret in the visitor's withdrawal link. The link no
  longer depends on the encryption key, so rotating RAG_ENCRYPTION_KEY cannot kill it. Leads
  created before this have none (no link was ever shown for a conversational lead; a form
  lead from before this revision can still be erased by its owner).
- `lead_consents.erased_at`: when the lead went, so the retention task keeps the consent
  record one year from then, not one year from the consent.

Downgrade drops both columns.

Revision ID: phase12_007
Revises: phase12_003
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase12_007"
down_revision: str | None = "phase12_003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("withdraw_hash", sa.String(64), nullable=True))
    op.add_column(
        "lead_consents", sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True)
    )
    # A record whose lead is already gone starts its year now.
    op.execute(sa.text("UPDATE lead_consents SET erased_at = now() WHERE lead_id IS NULL"))


def downgrade() -> None:
    op.drop_column("lead_consents", "erased_at")
    op.drop_column("leads", "withdraw_hash")
