"""First-run onboarding: wizard state, widget sightings, public assets (BIZ4).

Revision ID: phase15_002
Revises: phase15_001
Create Date: 2026-10-05

`onboarding_states` is one row per workspace (where the owner is in the three-step wizard and
the ids each step created). `widget_origin_seen` records the sites a widget key loaded from, so
the wizard can show a green light and name a refused `www` twin. `public_assets` holds the
business's uploaded avatars (re-encoded PNGs in object storage, served publicly by id).
Knowledge-base rows cascade with the knowledge base, workspace rows with the workspace; the
wizard's pointers to the knowledge base and key are SET NULL so deleting them never blocks.

Downgrade drops the three tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase15_002"
down_revision: str | None = "phase15_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")


def _ts(name: str, *, nullable: bool = True, default: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=_NOW if default else None,
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "onboarding_states",
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("status", sa.String(12), server_default=sa.text("'in_progress'"), nullable=False),
        sa.Column("step", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("website", sa.String(255), nullable=True),
        sa.Column("kb_id", _UUID, nullable=True),
        sa.Column("widget_key_id", _UUID, nullable=True),
        sa.Column("started_by", sa.String(320), nullable=False),
        _ts("created_at", nullable=False, default=True),
        _ts("updated_at", nullable=False, default=True),
        _ts("completed_at"),
        sa.PrimaryKeyConstraint("tenant_id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["kb_id"], ["corpora.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["widget_key_id"], ["widget_keys.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "status in ('in_progress','done','dismissed')", name="ck_onboarding_states_status"
        ),
        sa.CheckConstraint("step between 1 and 3", name="ck_onboarding_states_step"),
    )

    op.create_table(
        "widget_origin_seen",
        sa.Column("widget_key_id", _UUID, nullable=False),
        sa.Column("origin", sa.String(255), nullable=False),
        sa.Column("corpus_id", _UUID, nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        _ts("first_seen_at", nullable=False, default=True),
        _ts("last_seen_at", nullable=False, default=True),
        sa.PrimaryKeyConstraint("widget_key_id", "origin"),
        sa.ForeignKeyConstraint(["widget_key_id"], ["widget_keys.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["corpus_id"], ["corpora.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_widget_origin_seen_corpus_id", "widget_origin_seen", ["corpus_id"])

    op.create_table(
        "public_assets",
        sa.Column("id", _UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("corpus_id", _UUID, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(320), nullable=False),
        _ts("created_at", nullable=False, default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["corpus_id"], ["corpora.id"], ondelete="CASCADE"),
        sa.CheckConstraint("kind in ('avatar')", name="ck_public_assets_kind"),
    )
    op.create_index("ix_public_assets_tenant_id", "public_assets", ["tenant_id"])
    op.create_index("ix_public_assets_corpus_id", "public_assets", ["corpus_id"])


def downgrade() -> None:
    op.drop_table("public_assets")
    op.drop_table("widget_origin_seen")
    op.drop_table("onboarding_states")
