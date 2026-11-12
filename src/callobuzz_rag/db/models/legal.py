"""Legal pages (P3): the deployer's editable drafts and frozen published versions, and who
accepted which version at sign-up.

`version IS NULL` is the one draft per slug (partial unique index); publishing copies it into a
new row with `version = n + 1` and a trigger (`legal_documents_frozen`) refuses any UPDATE or
DELETE of a published row, so an acceptance always points at the exact text the person saw.
No corpus affinity: these are engine-level.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base
from callobuzz_rag.legal.slugs import SLUGS


def _slug_list() -> str:
    return ",".join(f"'{s}'" for s in SLUGS)


class LegalDocument(Base):
    __tablename__ = "legal_documents"
    __table_args__ = (
        UniqueConstraint("slug", "version", name="uq_legal_documents_slug_version"),
        Index(
            "uq_legal_documents_one_draft",
            "slug",
            unique=True,
            postgresql_where=text("version IS NULL"),
        ),
        CheckConstraint(f"slug in ({_slug_list()})", name="ck_legal_documents_slug"),
        CheckConstraint(
            "(version IS NULL) = (published_at IS NULL)", name="ck_legal_documents_published"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    slug: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    body_filled: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The draft's optimistic lock; published rows keep the value they were published at.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    change_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[str | None] = mapped_column(String(320), nullable=True)


class LegalAcceptance(Base):
    __tablename__ = "legal_acceptances"
    __table_args__ = (
        CheckConstraint("via in ('signup')", name="ck_legal_acceptances_via"),
        Index("ix_legal_acceptances_tenant_id", "tenant_id"),
        Index("ix_legal_acceptances_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_legal_acceptances_user_id_users"),
        nullable=True,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL", name="fk_legal_acceptances_tenant_id"),
        nullable=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "legal_documents.id", ondelete="RESTRICT", name="fk_legal_acceptances_document_id"
        ),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    via: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'signup'"))
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
