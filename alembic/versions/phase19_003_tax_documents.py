"""GST tax documents: invoices, bills of supply, credit notes, their counters (P2).

Revision ID: phase19_003
Revises: phase19_002
Create Date: 2026-10-08

- `tax_documents`: one row per tax invoice, bill of supply or credit note, with the seller and
  buyer frozen as JSON. NO foreign key on `tenant_id` / `account_id` / `payment_id`: the
  documents are kept for years after a workspace is deleted (like `audit_log`). The trigger
  `tax_documents_frozen` refuses every UPDATE except filling `pdf_key`, `pdf_sha256` and
  `emailed_at` once from NULL, and refuses DELETE always.
- `tax_document_counters`: the last number per (series, prefix, financial year), row-locked on
  every issue so numbers have no gaps.
- `payment_refunds.credit_note_skipped`: why a refund got no credit note (`deadline`).

Downgrade drops the tables and the column. It refuses while a tax document exists, because
issued tax documents must never be deleted.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase19_003"
down_revision: str | None = "phase19_002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")
_GEN = sa.text("gen_random_uuid()")


def upgrade() -> None:
    op.create_table(
        "tax_documents",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("doc_type", sa.String(16), nullable=False),
        sa.Column(
            "series",
            sa.String(12),
            sa.Computed(
                "CASE WHEN doc_type = 'credit_note' THEN 'credit_note' ELSE 'invoice' END",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column("number", sa.String(16), nullable=False),
        sa.Column("fy", sa.String(5), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("account_id", _UUID, nullable=False),
        sa.Column("payment_id", _UUID, nullable=False),
        sa.Column("refund_id", _UUID, nullable=True),
        sa.Column("original_id", _UUID, nullable=True),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("place_of_supply", sa.String(2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("taxable_minor", sa.BigInteger(), nullable=False),
        sa.Column("cgst_minor", sa.BigInteger(), nullable=False),
        sa.Column("sgst_minor", sa.BigInteger(), nullable=False),
        sa.Column("igst_minor", sa.BigInteger(), nullable=False),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("seller", postgresql.JSONB(), nullable=False),
        sa.Column("buyer", postgresql.JSONB(), nullable=False),
        sa.Column("lines", postgresql.JSONB(), nullable=False),
        sa.Column("reverse_charge", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("pdf_key", sa.Text(), nullable=True),
        sa.Column("pdf_sha256", sa.String(64), nullable=True),
        sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tax_documents"),
        sa.UniqueConstraint("series", "number", name="uq_tax_documents_series_number"),
        sa.CheckConstraint(
            "doc_type in ('invoice','bill_of_supply','credit_note')",
            name="ck_tax_documents_doc_type",
        ),
        sa.CheckConstraint(
            "(doc_type = 'credit_note') = (original_id IS NOT NULL AND refund_id IS NOT NULL)",
            name="ck_tax_documents_credit_note_links",
        ),
        sa.CheckConstraint(
            "doc_type <> 'credit_note' OR total_minor <= 0", name="ck_tax_documents_cn_negative"
        ),
        sa.CheckConstraint(
            "doc_type = 'credit_note' OR total_minor >= 0", name="ck_tax_documents_inv_positive"
        ),
        sa.CheckConstraint(
            "total_minor = taxable_minor + cgst_minor + sgst_minor + igst_minor",
            name="ck_tax_documents_total_sum",
        ),
    )
    op.create_index(
        "uq_tax_documents_payment_invoice",
        "tax_documents",
        ["payment_id"],
        unique=True,
        postgresql_where=sa.text("doc_type <> 'credit_note'"),
    )
    op.create_index(
        "uq_tax_documents_refund",
        "tax_documents",
        ["refund_id"],
        unique=True,
        postgresql_where=sa.text("refund_id IS NOT NULL"),
    )
    op.create_index("ix_tax_documents_tenant_issued", "tax_documents", ["tenant_id", "issued_at"])
    op.create_index("ix_tax_documents_fy_type", "tax_documents", ["fy", "doc_type"])
    op.create_index(
        "ix_tax_documents_pdf_missing",
        "tax_documents",
        ["created_at"],
        postgresql_where=sa.text("pdf_key IS NULL"),
    )

    op.execute(
        """
        CREATE FUNCTION tax_documents_frozen() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a tax document cannot be deleted';
            END IF;
            IF (OLD.pdf_key IS NOT NULL AND NEW.pdf_key IS DISTINCT FROM OLD.pdf_key)
               OR (OLD.pdf_sha256 IS NOT NULL AND NEW.pdf_sha256 IS DISTINCT FROM OLD.pdf_sha256)
               OR (OLD.emailed_at IS NOT NULL AND NEW.emailed_at IS DISTINCT FROM OLD.emailed_at)
               OR (to_jsonb(NEW) - 'pdf_key' - 'pdf_sha256' - 'emailed_at' - 'series')
                  IS DISTINCT FROM (to_jsonb(OLD) - 'pdf_key' - 'pdf_sha256' - 'emailed_at' - 'series')
            THEN
                RAISE EXCEPTION 'an issued tax document cannot be changed; issue a credit note';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER tax_documents_frozen BEFORE UPDATE OR DELETE ON tax_documents "
        "FOR EACH ROW EXECUTE FUNCTION tax_documents_frozen()"
    )

    op.create_table(
        "tax_document_counters",
        sa.Column("series", sa.String(12), nullable=False),
        sa.Column("prefix", sa.String(4), nullable=False),
        sa.Column("fy", sa.String(5), nullable=False),
        sa.Column("last_seq", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("series", "prefix", "fy", name="pk_tax_document_counters"),
    )
    op.add_column("payment_refunds", sa.Column("credit_note_skipped", sa.String(16), nullable=True))


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM tax_documents) THEN
                RAISE EXCEPTION 'issued tax documents exist; they must not be deleted';
            END IF;
        END $$
        """
    )
    op.drop_column("payment_refunds", "credit_note_skipped")
    op.drop_table("tax_document_counters")
    op.execute("DROP TRIGGER IF EXISTS tax_documents_frozen ON tax_documents")
    op.drop_table("tax_documents")
    op.execute("DROP FUNCTION IF EXISTS tax_documents_frozen()")
