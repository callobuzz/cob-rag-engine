"""Plan pricing: how customers on a plan pay (billing BIL2).

Versioned: every `PUT` on a plan's pricing inserts a new row (`version` n+1) and moves
`is_current` to it, in one transaction. Charges reference the version in force
(`usage_charges.pricing_id`), so a statement can explain an old price. A plan without a row has
no pricing: nothing is charged and nobody is paused (`billing/pricing.py` supplies the defaults).
Money columns are integer minor units of `currency` (paise / cents).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

_MONEY_COLUMNS = (
    "subscription_fee_minor",
    "per_question_minor",
    "byok_per_question_minor",
    "overage_per_question_minor",
    "credit_limit_minor",
    "grace_minor",
    "included_credit_minor",
)


class PlanPricing(Base):
    __tablename__ = "plan_pricing"
    __table_args__ = (
        UniqueConstraint("plan_id", "version", name="uq_plan_pricing_plan_version"),
        Index("ix_plan_pricing_plan_effective", "plan_id", "effective_at"),  # TV4 schema
        Index(
            "uq_plan_pricing_current",
            "plan_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        CheckConstraint("currency in ('INR','USD')", name="ck_plan_pricing_currency"),
        CheckConstraint("billing_period in ('monthly','yearly')", name="ck_plan_pricing_period"),
        CheckConstraint(
            "usage_mode in ('included','per_question','cost_plus')", name="ck_plan_pricing_usage"
        ),
        CheckConstraint("byok_mode in ('free','per_question')", name="ck_plan_pricing_byok"),
        CheckConstraint("overage in ('block','charge','allow')", name="ck_plan_pricing_overage"),
        CheckConstraint(
            "overage_mode in ('per_question','cost_plus')", name="ck_plan_pricing_overage_mode"
        ),
        CheckConstraint(
            "payment_type in ('prepaid','postpaid')", name="ck_plan_pricing_payment_type"
        ),
        CheckConstraint("version >= 1", name="ck_plan_pricing_version"),
        CheckConstraint("trial_days BETWEEN 0 AND 365", name="ck_plan_pricing_trial_days"),
        CheckConstraint("markup_bp BETWEEN 0 AND 100000", name="ck_plan_pricing_markup"),
        *(
            CheckConstraint(f"{column} >= 0", name=f"ck_plan_pricing_{column}")
            for column in _MONEY_COLUMNS
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="CASCADE", name="fk_plan_pricing_plan_id_plans"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subscription_fee_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    billing_period: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'monthly'")
    )
    trial_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    usage_mode: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'included'")
    )
    per_question_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    markup_bp: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    byok_mode: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'free'")
    )
    byok_per_question_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    byok_counts_toward_caps: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    overage: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'block'"))
    overage_mode: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'per_question'")
    )
    overage_per_question_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    payment_type: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'prepaid'")
    )
    credit_limit_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    grace_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    included_credit_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    included_credit_expires: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    alert_thresholds_pct: Mapped[list[int]] = mapped_column(
        ARRAY(SmallInteger), nullable=False, server_default=text("'{20,5,0}'::smallint[]")
    )
    members_see_money: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # TV4 schema: NULL = in force from creation; a later time = a scheduled price change (TV3)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # /TV4 schema
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
