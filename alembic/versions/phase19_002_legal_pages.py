"""Legal pages: editable drafts, frozen published versions, sign-up acceptances (P3).

Revision ID: phase19_002
Revises: phase19_001
Create Date: 2026-10-08

- `legal_documents`: one draft per slug (`version IS NULL`, partial unique index) and any number
  of published versions. A trigger refuses UPDATE and DELETE of a published row, so an
  acceptance always points at the exact text the person saw (same idea as the wallet ledger).
- `legal_acceptances`: which Terms / Privacy version each person accepted at sign-up. The user
  and workspace links are SET NULL so the record outlives them (the e-mail is kept); the
  document link is RESTRICT.
- No corpus affinity: these are engine-level tables.

Downgrade drops the acceptances, then the documents, trigger and function.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase19_002"
down_revision: str | None = "phase19_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")
_GEN = sa.text("gen_random_uuid()")
_SLUGS = (
    "terms",
    "privacy",
    "refunds",
    "delivery",
    "acceptable-use",
    "dpa",
    "sub-processors",
    "grievance",
)


def upgrade() -> None:
    slug_list = ",".join(f"'{s}'" for s in _SLUGS)
    op.create_table(
        "legal_documents",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("slug", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column("body_filled", sa.Text(), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("change_note", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.String(320), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_legal_documents"),
        sa.UniqueConstraint("slug", "version", name="uq_legal_documents_slug_version"),
        sa.CheckConstraint(f"slug in ({slug_list})", name="ck_legal_documents_slug"),
        sa.CheckConstraint(
            "(version IS NULL) = (published_at IS NULL)", name="ck_legal_documents_published"
        ),
    )
    op.create_index(
        "uq_legal_documents_one_draft",
        "legal_documents",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("version IS NULL"),
    )
    op.execute(
        """
        CREATE FUNCTION legal_documents_frozen() RETURNS trigger AS $$
        BEGIN
            IF OLD.published_at IS NOT NULL THEN
                RAISE EXCEPTION 'a published legal page version cannot be changed or deleted';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER legal_documents_frozen BEFORE UPDATE OR DELETE ON legal_documents "
        "FOR EACH ROW EXECUTE FUNCTION legal_documents_frozen()"
    )

    op.create_table(
        "legal_acceptances",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("user_id", _UUID, nullable=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=True),
        sa.Column("document_id", _UUID, nullable=False),
        sa.Column("slug", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("via", sa.String(16), server_default=sa.text("'signup'"), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_legal_acceptances"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_legal_acceptances_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_legal_acceptances_tenant_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["legal_documents.id"],
            name="fk_legal_acceptances_document_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("via in ('signup')", name="ck_legal_acceptances_via"),
    )
    op.create_index("ix_legal_acceptances_tenant_id", "legal_acceptances", ["tenant_id"])
    op.create_index("ix_legal_acceptances_user_id", "legal_acceptances", ["user_id"])


def downgrade() -> None:
    op.drop_table("legal_acceptances")
    op.execute("DROP TRIGGER IF EXISTS legal_documents_frozen ON legal_documents")
    op.drop_table("legal_documents")
    op.execute("DROP FUNCTION IF EXISTS legal_documents_frozen()")
