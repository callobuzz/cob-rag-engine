"""Named API keys with permissions.

`api_keys`: many named keys per knowledge base (`ck_`) and per customer (`tk_`), each with a
permission (`read` | `write` | `full`), a shown prefix, who created it and when it was last
used. Each existing single key (`corpora.api_key_hash`, `tenants.api_key_hash`) is copied in
as the owner's "Default key" with the SAME hash and `full` permission, so nothing is re-issued
and every key keeps working exactly as before; the old columns are then blanked so there is
one source of truth. Downgrade copies each default key's hash back and drops the table (named
keys other than the default are lost).

Revision ID: phase11_012
Revises: phase11_011
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase11_012"
down_revision: str | None = "phase11_011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "corpus_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("corpora.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("id_short", sa.String(length=8), nullable=False),
        sa.Column("lookup", sa.String(length=8), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("key_prefix", sa.String(length=40), nullable=False),
        sa.Column("key_hash", sa.String(length=255), nullable=False),
        sa.Column("permission", sa.String(length=8), nullable=False, server_default="full"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_by", sa.String(length=320), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("now()")
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(corpus_id IS NOT NULL) <> (tenant_id IS NOT NULL)", name="ck_api_keys_one_owner"
        ),
        sa.CheckConstraint("permission in ('read','write','full')", name="ck_api_keys_permission"),
    )
    op.create_index("ix_api_keys_id_short", "api_keys", ["id_short"])
    op.create_index("ix_api_keys_corpus_id", "api_keys", ["corpus_id"])
    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])
    op.create_index(
        "uq_api_keys_default_corpus",
        "api_keys",
        ["corpus_id"],
        unique=True,
        postgresql_where=sa.text("is_default AND corpus_id IS NOT NULL"),
    )
    op.create_index(
        "uq_api_keys_default_tenant",
        "api_keys",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("is_default AND tenant_id IS NOT NULL"),
    )

    # Each existing key becomes its owner's "Default key": same hash, full permission, no
    # lookup segment (the plaintext was never stored). A corpus key's id segment is the one
    # in its collection name, which is the first 8 characters of its id.
    op.execute(
        """
        INSERT INTO api_keys (corpus_id, id_short, name, key_prefix, key_hash, permission,
                              is_default, created_by, created_at)
        SELECT id,
               CASE WHEN starts_with(qdrant_collection, 'corpus_') THEN substr(qdrant_collection, 8, 8)
                    ELSE left(id::text, 8) END,
               'Default key',
               'ck_' || CASE WHEN starts_with(qdrant_collection, 'corpus_')
                             THEN substr(qdrant_collection, 8, 8)
                             ELSE left(id::text, 8) END || '_...',
               api_key_hash, 'full', true, 'migrated', created_at
        FROM corpora WHERE api_key_hash <> ''
        """
    )
    op.execute(
        """
        INSERT INTO api_keys (tenant_id, id_short, name, key_prefix, key_hash, permission,
                              is_default, created_by, created_at)
        SELECT id, left(id::text, 8), 'Default key', 'tk_' || left(id::text, 8) || '_...',
               api_key_hash, 'full', true, 'migrated', created_at
        FROM tenants WHERE api_key_hash <> ''
        """
    )
    op.execute("UPDATE corpora SET api_key_hash = '' WHERE api_key_hash <> ''")
    op.execute("UPDATE tenants SET api_key_hash = '' WHERE api_key_hash <> ''")


def downgrade() -> None:
    op.execute(
        """
        UPDATE corpora SET api_key_hash = k.key_hash
        FROM api_keys k WHERE k.corpus_id = corpora.id AND k.is_default
        """
    )
    op.execute(
        """
        UPDATE tenants SET api_key_hash = k.key_hash
        FROM api_keys k WHERE k.tenant_id = tenants.id AND k.is_default
        """
    )
    op.drop_index("uq_api_keys_default_tenant", table_name="api_keys")
    op.drop_index("uq_api_keys_default_corpus", table_name="api_keys")
    op.drop_index("ix_api_keys_tenant_id", table_name="api_keys")
    op.drop_index("ix_api_keys_corpus_id", table_name="api_keys")
    op.drop_index("ix_api_keys_id_short", table_name="api_keys")
    op.drop_table("api_keys")
