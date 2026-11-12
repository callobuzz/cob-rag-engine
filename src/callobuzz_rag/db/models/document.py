"""Document model — raw uploaded documents."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from callobuzz_rag.db.base import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("corpora.id", ondelete="CASCADE"))
    source_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Caller-supplied record id for text imported via POST /ingest/records. Unique per
    # corpus, so re-sending a record replaces it instead of duplicating it.
    external_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # The metadata given at ingest (category, user_id, custom keys). Qdrant payloads
    # carry it for filtering; this copy is what lets a rebuild carry it again.
    doc_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    # Indexes for query performance
    __table_args__ = (
        Index("ix_documents_content_fingerprint", "content_fingerprint"),
        Index("ix_documents_corpus_id", "corpus_id"),
        Index(
            "uq_documents_corpus_external_id",
            "corpus_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
    )

    # Relationships
    corpus: Mapped["Corpus"] = relationship(back_populates="documents")  # type: ignore[name-defined]
    versions: Mapped[list["DocumentVersion"]] = relationship(  # type: ignore[name-defined]
        back_populates="document", cascade="all, delete-orphan"
    )
