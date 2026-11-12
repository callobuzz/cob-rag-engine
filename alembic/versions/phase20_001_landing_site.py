"""The public website (LP1): the site row, frozen versions and uploaded assets.

Revision ID: phase20_001
Revises: phase19_004
Create Date: 2026-10-10

- `landing_sites`: exactly one row per install (primary key `id` with a check `id = 1`; one
  deployer per install). The row is created lazily by the engine with the shipped default
  content, never here, so a change of the default never needs a data migration. `enabled` is
  false until the operator publishes and switches the site on. The two "Ask it yourself" links
  are SET NULL: deleting that knowledge base or widget key only hides the button.
- `landing_versions`: every published snapshot (content, derived theme, frozen CSS, hash).
- `landing_assets`: logo, poster, video and Open Graph images, with checked types and sizes.
- No corpus affinity (engine-level tables).

Downgrade drops the three tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase20_001"
down_revision: str | None = "phase19_004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")
_GEN = sa.text("gen_random_uuid()")


def upgrade() -> None:
    op.create_table(
        "landing_sites",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("address", sa.String(300), nullable=True),
        sa.Column("draft", postgresql.JSONB(), nullable=False),
        sa.Column("draft_revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("draft_theme", postgresql.JSONB(), nullable=False),
        sa.Column("published_version", sa.Integer(), nullable=True),
        sa.Column("ask_corpus_id", _UUID, nullable=True),
        sa.Column("ask_widget_key_id", _UUID, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_landing_sites"),
        sa.ForeignKeyConstraint(
            ["ask_corpus_id"],
            ["corpora.id"],
            name="fk_landing_sites_ask_corpus_id_corpora",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ask_widget_key_id"],
            ["widget_keys.id"],
            name="fk_landing_sites_ask_widget_key_id_widget_keys",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("id = 1", name="ck_landing_sites_single"),
    )
    op.create_table(
        "landing_versions",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("theme", postgresql.JSONB(), nullable=False),
        sa.Column("theme_css", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("change_note", sa.String(300), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("published_by", sa.String(320), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_landing_versions"),
        sa.UniqueConstraint("version", name="uq_landing_versions_version"),
    )
    op.create_table(
        "landing_assets",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("content_type", sa.String(40), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(300), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_landing_assets"),
        sa.CheckConstraint("kind in ('logo','poster','video','og')", name="ck_landing_assets_kind"),
        sa.CheckConstraint(
            "content_type in ('image/png','image/webp','video/mp4','video/webm')",
            name="ck_landing_assets_content_type",
        ),
    )


def downgrade() -> None:
    op.drop_table("landing_assets")
    op.drop_table("landing_versions")
    op.drop_table("landing_sites")
