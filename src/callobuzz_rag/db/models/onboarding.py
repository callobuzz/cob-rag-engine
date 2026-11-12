"""First-run onboarding state and where a widget key was first seen loading (BIZ4)."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

ONBOARDING_STATUSES = ("in_progress", "done", "dismissed")

_NOW = text("now()")


class OnboardingState(Base):
    """One row per workspace: the wizard's step and the ids each step created."""

    __tablename__ = "onboarding_states"
    __table_args__ = (
        CheckConstraint(
            "status in ('in_progress','done','dismissed')", name="ck_onboarding_states_status"
        ),
        CheckConstraint("step between 1 and 4", name="ck_onboarding_states_step"),  # TV5 brochure
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'in_progress'"), default="in_progress"
    )
    step: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1"), default=1
    )
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kb_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("corpora.id", ondelete="SET NULL"), nullable=True
    )
    widget_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("widget_keys.id", ondelete="SET NULL"), nullable=True
    )
    started_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WidgetOriginSeen(Base):
    """A site a widget key loaded from (allowed or refused), for the install light."""

    __tablename__ = "widget_origin_seen"

    widget_key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("widget_keys.id", ondelete="CASCADE"), primary_key=True
    )
    origin: Mapped[str] = mapped_column(String(255), primary_key=True)
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("corpora.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
