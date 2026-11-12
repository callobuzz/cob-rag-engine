"""Workspace model — central container for content preparation."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from callobuzz_rag.db.base import Base


class WorkspaceStatus(StrEnum):
    """Workspace lifecycle states."""

    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    INGESTED = "ingested"
    ARCHIVED = "archived"


class WorkflowMode(StrEnum):
    """Review workflow modes."""

    SINGLE_REVIEWER = "single_reviewer"
    MAKER_CHECKER = "maker_checker"


class WorkspaceKind(StrEnum):
    """What a workspace is for. Set at creation and never edited."""

    STANDARD = "standard"  # authoring: staged content → chunks → a new document
    POST_INGEST_REVIEW = "post_ingest_review"  # reviewing an already-ingested document


class Workspace(Base):
    __tablename__ = "portal_workspaces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("corpora.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=WorkspaceKind.STANDARD,
        server_default=text("'standard'"),
    )
    reviewed_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Post-ingest review: the document version the review was created from.",
    )
    finalized_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="SET NULL"),
        nullable=True,
        comment="Post-ingest review: the document version finalizing it produced.",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=WorkspaceStatus.DRAFT,
        server_default=text("'draft'"),
    )
    workflow_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=WorkflowMode.SINGLE_REVIEWER,
        server_default=text("'single_reviewer'"),
    )
    created_by: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    assigned_reviewer: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    locked_by: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    review_feedback: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
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
    staged_contents: Mapped[list["StagedContent"]] = relationship(  # type: ignore[name-defined]
        back_populates="workspace",
        cascade="all, delete-orphan",
    )
    staged_documents: Mapped[list["StagedDocument"]] = relationship(  # type: ignore[name-defined]
        back_populates="workspace",
        cascade="all, delete-orphan",
    )
    staged_chunks: Mapped[list["StagedChunk"]] = relationship(  # type: ignore[name-defined]
        back_populates="workspace",
        cascade="all, delete-orphan",
    )
    conversations: Mapped[list["WorkspaceConversation"]] = relationship(  # type: ignore[name-defined]
        back_populates="workspace",
        cascade="all, delete-orphan",
    )
