"""Guided setup sessions (conversation layer C8): one owner's walk through the setup chat.

A session moves started -> proposed -> interviewing -> generated -> tested -> applied, or is
abandoned when a newer session starts for the same knowledge base. It stores what the owner
confirmed (`profile`) and answered and what was generated from it (`provenance` maps each
generated config path to the question id, "crawl", "confirmed" or "template" that produced it).
"""

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

SETUP_STATUSES: Final[tuple[str, ...]] = (
    "started",
    "proposed",
    "interviewing",
    "generated",
    "tested",
    "applied",
    "abandoned",
)


class AssistantSetupSession(Base):
    __tablename__ = "assistant_setup_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ",".join(f"'{s}'" for s in SETUP_STATUSES) + ")",
            name="ck_assistant_setup_sessions_status",
        ),
        Index("ix_assistant_setup_sessions_corpus_created", "corpus_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="started", server_default=text("'started'")
    )
    business_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    website_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    crawl_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proposal: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # The owner-confirmed business name, phone, WhatsApp, e-mail, address, website and hours.
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    answers: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    generated_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    provenance: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    golden_set_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("golden_sets.id", ondelete="SET NULL"), nullable=True
    )
    eval_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("eval_run.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )
