"""IngestJob model — ingestion job tracking."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class IngestJob(Base):
    __tablename__ = "ingest_jobs"
    __table_args__ = (
        # The corpus list's last activity is max(created_at) per corpus (phase10_001).
        Index("ix_ingest_jobs_corpus_id_created_at", "corpus_id", "created_at"),
        # An idempotency key is scoped to its corpus (phase10_005): another tenant's key
        # is neither a clash nor a way to learn that it exists.
        UniqueConstraint(
            "corpus_id", "idempotency_key", name="uq_ingest_jobs_corpus_idempotency_key"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    status: Mapped[str] = mapped_column(String(20), server_default=text("'queued'"))
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    total_documents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_documents: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    errors: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Phase 4: Link job to corpus for scoped listing
    corpus_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Phase 6: Re-ingestion metadata
    mode: Mapped[str] = mapped_column(String(20), server_default=text("'auto'"))
    target_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    review_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Accounts A6: how many new documents this job will add at most, set when it is
    # queued (records: ids not yet in the corpus; upload new/auto: 1; replace/version of
    # an existing document: 0). While the job is in flight it counts against the
    # tenant's document limit (`accounts/caps.py`). NULL = not computed (unlimited plan).
    pending_new_documents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    @property
    def file_name(self) -> str | None:
        """The uploaded file's name (none for a records import)."""
        name: str | None = (self.review_metadata or {}).get("file_name")
        return name

    @property
    def similar_documents(self) -> list[dict[str, Any]] | None:
        """Existing documents a `pending_review` upload resembles, most similar first."""
        similar: list[dict[str, Any]] | None = (self.review_metadata or {}).get("similar_documents")
        return similar

    @property
    def records(self) -> dict[str, int] | None:
        """created/updated/unchanged/chunks counts for a POST /ingest/records job."""
        counts: dict[str, int] | None = (self.review_metadata or {}).get("records")
        return counts
