"""Value report: the owner's value settings and the frozen monthly reports (TV4).

`value_settings` holds one row per workspace (created by the first save); money is integer minor
units of `currency`. `value_reports` freezes a closed month's numbers and the settings used, so
the page and the e-mail for that month never change afterwards.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class ValueSettings(Base):
    __tablename__ = "value_settings"
    __table_args__ = (
        CheckConstraint("currency in ('INR','USD')", name="ck_value_settings_currency"),
        CheckConstraint("lead_value_minor >= 0", name="ck_value_settings_lead"),
        CheckConstraint("handoff_value_minor >= 0", name="ck_value_settings_handoff"),
        CheckConstraint("staff_hour_minor >= 0", name="ck_value_settings_staff"),
        CheckConstraint("minutes_per_answer BETWEEN 0 AND 120", name="ck_value_settings_minutes"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_value_settings_tenant_id_tenants"),
        primary_key=True,
    )
    lead_value_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), default=0
    )
    handoff_value_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), default=0
    )
    staff_hour_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), default=0
    )
    minutes_per_answer: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("3"), default=3
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    email_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    industry_defaults: Mapped[str | None] = mapped_column(String(20), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )
    updated_by: Mapped[str | None] = mapped_column(String(320), nullable=True)


class ValueReport(Base):
    __tablename__ = "value_reports"
    __table_args__ = (UniqueConstraint("tenant_id", "month", name="uq_value_reports_tenant_month"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_value_reports_tenant_id_tenants"),
        nullable=False,
    )
    month: Mapped[date] = mapped_column(Date, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # addresses the e-mail was queued to (lower-case); `emailed_at` is set when all of them are
    emailed_to: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
