"""StagedChunk model — working chunks for editing and review."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from callobuzz_rag.db.base import Base


class StagedChunkStatus(StrEnum):
    """Staged chunk review states."""

    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"


class StagedChunkSource(StrEnum):
    """How the chunk was created."""

    AI_GENERATED = "ai_generated"
    MANUAL = "manual"
    FROM_INGESTION = "from_ingestion"


class StagedChunk(Base):
    __tablename__ = "portal_staged_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=sa_text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("portal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    staged_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("portal_staged_documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )
    confidence_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    warnings: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=StagedChunkStatus.DRAFT,
        server_default=sa_text("'draft'"),
    )
    source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=StagedChunkSource.AI_GENERATED,
        server_default=sa_text("'ai_generated'"),
    )
    edit_history: Mapped[list[Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=sa_text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=sa_text("now()"),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    workspace: Mapped["Workspace"] = relationship(  # type: ignore[name-defined]
        back_populates="staged_chunks",
    )
    staged_document: Mapped["StagedDocument | None"] = relationship(  # type: ignore[name-defined]
        back_populates="staged_chunks",
    )
