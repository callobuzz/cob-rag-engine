"""Per-plan upload and document-size caps.

`plans.max_upload_mb` (largest single upload) and `plans.max_document_tokens` (most text one
document may hold, counted after parsing). NULL = the engine default
(`RAG_MAX_UPLOAD_BYTES`, `RAG_MAX_DOCUMENT_TOKENS`); a plan can only set a LOWER cap.

Revision ID: phase11_010
Revises: phase11_009
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase11_010"
down_revision: str | None = "phase11_009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("max_upload_mb", sa.Integer(), nullable=True))
    op.add_column("plans", sa.Column("max_document_tokens", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_plans_max_upload_mb", "plans", "max_upload_mb IS NULL OR max_upload_mb >= 1"
    )
    op.create_check_constraint(
        "ck_plans_max_document_tokens",
        "plans",
        "max_document_tokens IS NULL OR max_document_tokens >= 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_plans_max_document_tokens", "plans", type_="check")
    op.drop_constraint("ck_plans_max_upload_mb", "plans", type_="check")
    op.drop_column("plans", "max_document_tokens")
    op.drop_column("plans", "max_upload_mb")
