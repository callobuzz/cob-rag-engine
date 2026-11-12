"""GST tax documents (P2): invoices, bills of supply and credit notes, frozen once issued.

`tenant_id` / `account_id` / `payment_id` / `refund_id` carry NO foreign keys on purpose: a tax
document is kept for years after a workspace is deleted (like `audit_log`). A database trigger
refuses every change except filling `pdf_key`, `pdf_sha256` and `emailed_at` once, and refuses
DELETE always. A correction is a credit note, never an edit.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

_NOW = text("now()")
_GEN = text("gen_random_uuid()")


class TaxDocument(Base):
    """One tax invoice, bill of supply or credit note, with its seller and buyer frozen."""

    __tablename__ = "tax_documents"
    __table_args__ = (
        CheckConstraint(
            "doc_type in ('invoice','bill_of_supply','credit_note')",
            name="ck_tax_documents_doc_type",
        ),
        CheckConstraint(
            "(doc_type = 'credit_note') = (original_id IS NOT NULL AND refund_id IS NOT NULL)",
            name="ck_tax_documents_credit_note_links",
        ),
        CheckConstraint(
            "doc_type <> 'credit_note' OR total_minor <= 0", name="ck_tax_documents_cn_negative"
        ),
        CheckConstraint(
            "doc_type = 'credit_note' OR total_minor >= 0", name="ck_tax_documents_inv_positive"
        ),
        CheckConstraint(
            "total_minor = taxable_minor + cgst_minor + sgst_minor + igst_minor",
            name="ck_tax_documents_total_sum",
        ),
        UniqueConstraint("series", "number", name="uq_tax_documents_series_number"),
        Index(
            "uq_tax_documents_payment_invoice",
            "payment_id",
            unique=True,
            postgresql_where=text("doc_type <> 'credit_note'"),
        ),
        Index(
            "uq_tax_documents_refund",
            "refund_id",
            unique=True,
            postgresql_where=text("refund_id IS NOT NULL"),
        ),
        Index("ix_tax_documents_tenant_issued", "tenant_id", "issued_at"),
        Index("ix_tax_documents_fy_type", "fy", "doc_type"),
        Index(
            "ix_tax_documents_pdf_missing",
            "created_at",
            postgresql_where=text("pdf_key IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=_GEN)
    doc_type: Mapped[str] = mapped_column(String(16), nullable=False)
    series: Mapped[str] = mapped_column(
        String(12),
        Computed(
            "CASE WHEN doc_type = 'credit_note' THEN 'credit_note' ELSE 'invoice' END",
            persisted=True,
        ),
        nullable=False,
    )
    number: Mapped[str] = mapped_column(String(16), nullable=False)
    fy: Mapped[str] = mapped_column(String(5), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    refund_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    original_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    place_of_supply: Mapped[str] = mapped_column(String(2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    taxable_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sgst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    igst_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seller: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    buyer: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    reverse_charge: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    pdf_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)


class TaxDocumentCounter(Base):
    """The last number used in a series, prefix and financial year (row-locked on every issue)."""

    __tablename__ = "tax_document_counters"

    series: Mapped[str] = mapped_column(String(12), primary_key=True)
    prefix: Mapped[str] = mapped_column(String(4), primary_key=True)
    fy: Mapped[str] = mapped_column(String(5), primary_key=True)
    last_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
