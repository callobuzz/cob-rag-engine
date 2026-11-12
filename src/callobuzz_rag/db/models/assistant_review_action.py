"""What an owner did about a review-inbox item (conversation C7)."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

REVIEW_KINDS = ("no_answer", "low_confidence", "thumbs_down", "handoff")
REVIEW_ACTIONS = ("dismissed", "added_document", "added_answer", "added_rule")


class AssistantReviewAction(Base):
    __tablename__ = "assistant_review_actions"
    __table_args__ = (
        CheckConstraint(
            "action IN ('dismissed', 'added_document', 'added_answer', 'added_rule')",
            name="ck_review_actions_action",
        ),
        CheckConstraint(
            "kind IN ('no_answer', 'low_confidence', 'thumbs_down', 'handoff')",
            name="ck_review_actions_kind",
        ),
        Index("ix_review_actions_corpus_key", "corpus_id", "item_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False
    )
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    target: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
