"""What the super admin set for one LLM provider's balance (billing BIL1).

OpenRouter, OpenAI and Anthropic have at most one row each; `manual` rows are free-form (a
label such as "Groq") and there can be several. `admin_key_encrypted` is a Fernet token made by
`core/secrets.py`; no API ever returns it or the key. `alert_state` remembers the last alerted
level so the check job mails once per crossing.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

PROVIDER_IDS = ("openrouter", "openai", "anthropic", "manual")


class ProviderBalanceSetting(Base):
    __tablename__ = "provider_balance_settings"
    __table_args__ = (
        CheckConstraint(
            "provider in ('openrouter','openai','anthropic','manual')",
            name="ck_provider_balance_settings_provider",
        ),
        CheckConstraint(
            "manual_balance_usd_micro IS NULL OR manual_balance_usd_micro >= 0",
            name="ck_provider_balance_settings_balance",
        ),
        CheckConstraint(
            "(manual_balance_usd_micro IS NULL) = (manual_balance_at IS NULL)",
            name="ck_provider_balance_settings_balance_pair",
        ),
        CheckConstraint(
            "manual_note IS NULL OR char_length(manual_note) <= 500",
            name="ck_provider_balance_settings_note",
        ),
        CheckConstraint(
            "low_threshold_usd_micro IS NULL OR low_threshold_usd_micro >= 0",
            name="ck_provider_balance_settings_threshold",
        ),
        CheckConstraint(
            "alert_state in ('ok','low','empty')",
            name="ck_provider_balance_settings_alert_state",
        ),
        Index(
            "uq_provider_balance_settings_provider",
            "provider",
            unique=True,
            postgresql_where=text("provider <> 'manual'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    admin_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    manual_balance_usd_micro: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    manual_balance_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    manual_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    low_threshold_usd_micro: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    alert_state: Mapped[str] = mapped_column(
        String(8), nullable=False, default="ok", server_default=text("'ok'")
    )
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), default=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
