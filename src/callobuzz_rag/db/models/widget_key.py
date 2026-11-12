"""Widget key — a publishable (pk_) key that lets a website chat with one corpus.

Unlike corpus (ck_) and tenant (tk_) keys this one is PUBLIC by design: it sits in
the HTML of the customer's website, so anyone can read it. It is stored in plain
text (it is not a secret, and the dashboard shows it again for the embed
snippet) and it can do exactly one thing — ask its corpus questions through the
widget endpoints. What protects it is not secrecy but:

- allowed_origins: the browser Origin must be one of the customer's sites;
- per-visitor and per-key-per-day rate limits, so a scraped key cannot drain the
  corpus owner's LLM budget.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from callobuzz_rag.db.base import Base


class WidgetKey(Base):
    __tablename__ = "widget_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    corpus_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("corpora.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    public_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="Publishable pk_ key (not a secret)",
    )
    allowed_origins: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment="Exact browser origins allowed to use this key, e.g. https://acme.com",
    )
    rate_limit_per_minute: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("10"),
        comment="Messages per visitor per minute",
    )
    daily_message_limit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1000"),
        comment="Messages per key per UTC day, across all visitors (0 = unlimited)",
    )
    appearance: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="Widget branding: title, greeting, primary_color, position, placeholder",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    corpus: Mapped["Corpus"] = relationship()  # type: ignore[name-defined]
