"""StagedDocument model — AI-restructured documents for review."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from callobuzz_rag.db.base import Base


class StagedDocumentStatus(StrEnum):
    """Staged document review states."""

    DRAFT = "draft"
    APPROVED = "approved"


class StagedDocument(Base):
    __tablename__ = "portal_staged_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("portal_workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    staged_content_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("portal_staged_content.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    structured_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=StagedDocumentStatus.DRAFT,
        server_default=text("'draft'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    workspace: Mapped["Workspace"] = relationship(  # type: ignore[name-defined]
        back_populates="staged_documents",
    )
    staged_content: Mapped["StagedContent"] = relationship(  # type: ignore[name-defined]
        back_populates="staged_documents",
    )
    staged_chunks: Mapped[list["StagedChunk"]] = relationship(  # type: ignore[name-defined]
        back_populates="staged_document",
        cascade="all, delete-orphan",
    )
