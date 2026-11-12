"""ContentRule model — per-corpus content routing rules."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class RuleType(StrEnum):
    """Content routing rule types."""

    MIME_TYPE = "mime_type"
    SIZE = "size"
    CONFIDENCE = "confidence"
    ALWAYS = "always"


class RuleAction(StrEnum):
    """Actions taken when a rule matches."""

    STAGE = "stage"
    DIRECT_INGEST = "direct_ingest"


class ContentRule(Base):
    __tablename__ = "portal_content_rules"

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
    rule_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    condition: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
    )
    action: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=RuleAction.STAGE,
        server_default=text("'stage'"),
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=100,
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
    )
