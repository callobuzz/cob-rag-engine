"""StagedContent model — raw uploads before AI processing."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from callobuzz_rag.db.base import Base


class AIProcessingStatus(StrEnum):
    """AI processing lifecycle states."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class SourceType(StrEnum):
    """Content source types."""

    FILE = "file"
    TEXT_DUMP = "text_dump"
    URL = "url"
    SCRAPED = "scraped"


class StagedContent(Base):
    __tablename__ = "portal_staged_content"

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
    source_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    original_filename: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    mime_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    raw_storage_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    ai_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=AIProcessingStatus.PENDING,
        server_default=text("'pending'"),
    )
    ai_output_type: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )
    ai_quality_report: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )

    # Relationships
    workspace: Mapped["Workspace"] = relationship(  # type: ignore[name-defined]
        back_populates="staged_contents",
    )
    staged_documents: Mapped[list["StagedDocument"]] = relationship(  # type: ignore[name-defined]
        back_populates="staged_content",
        cascade="all, delete-orphan",
    )
