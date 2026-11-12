"""DocumentVersion model — immutable processing versions."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from callobuzz_rag.db.base import Base


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    parser_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    chunk_schema_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'processing'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_document_versions_document_id", "document_id"),
        # At most one active version per document (phase10_004): two would both be
        # searchable, with duplicate or contradictory results.
        Index(
            "uq_document_versions_one_active",
            "document_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    # Relationships
    document: Mapped["Document"] = relationship(back_populates="versions")  # type: ignore[name-defined]
    chunks: Mapped[list["Chunk"]] = relationship(  # type: ignore[name-defined]
        back_populates="document_version", cascade="all, delete-orphan"
    )
