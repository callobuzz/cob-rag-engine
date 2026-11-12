"""The add-on catalogue and the grants made from it (billing BIL5).

An add-on is something the super admin sells on top of a plan: a credit pack (money), extra
limits (+N knowledge bases, documents, widget keys, upload MB or document tokens, for this month
or for good) or a question pack (+N questions this month). A grant is one add-on applied to one
workspace; it copies the add-on's fields (a snapshot), so editing the catalogue later never
rewrites history. Effective limits are the plan's, the account's overrides, plus the active
grants (`billing/limits.py`).
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base

LIMIT_FIELD_CHECK = (
    "limit_field in ('max_knowledge_bases','max_documents','max_widget_keys','max_upload_mb',"
    "'max_document_tokens')"
)

# P5 plans
ADD_ON_KINDS = ("credit_pack", "extra_limit", "question_pack", "feature_unlock", "service")
KIND_LIST = ",".join(f"'{k}'" for k in ADD_ON_KINDS)
# /P5 plans

# Exactly the fields of the kind are set (the same rule on an add-on and on its grant).
KIND_FIELDS_CHECK = (
    "(kind = 'credit_pack' AND credit_minor IS NOT NULL AND credit_minor > 0"
    " AND limit_field IS NULL AND limit_delta IS NULL AND questions IS NULL AND feature IS NULL)"
    " OR (kind = 'extra_limit' AND credit_minor IS NULL AND limit_field IS NOT NULL"
    " AND limit_delta IS NOT NULL AND questions IS NULL AND feature IS NULL)"
    " OR (kind = 'question_pack' AND credit_minor IS NULL AND limit_field IS NULL"
    " AND limit_delta IS NULL AND questions IS NOT NULL AND feature IS NULL)"
    # P5 plans
    " OR (kind = 'feature_unlock' AND credit_minor IS NULL AND limit_field IS NULL"
    " AND limit_delta IS NULL AND questions IS NULL AND feature IS NOT NULL)"
    " OR (kind = 'service' AND credit_minor IS NULL AND limit_field IS NULL"
    " AND limit_delta IS NULL AND questions IS NULL AND feature IS NULL)"
    # /P5 plans
)
# P5 plans: a renewing add-on is a monthly own-key unlock or a service (priority support)
RENEWS_CHECK = "NOT renews OR kind = 'service' OR (kind = 'feature_unlock' AND duration = 'period')"
SAC_RE = "^99[0-9]{4}$"
# /P5 plans


class AddOn(Base):
    __tablename__ = "add_on_catalogue"
    __table_args__ = (
        UniqueConstraint("name", name="uq_add_on_catalogue_name"),
        CheckConstraint(f"kind in ({KIND_LIST})", name="ck_add_on_kind"),  # P5 plans
        CheckConstraint("currency in ('INR','USD')", name="ck_add_on_currency"),
        CheckConstraint("price_minor >= 0", name="ck_add_on_price"),
        CheckConstraint("bonus_pct BETWEEN 0 AND 100", name="ck_add_on_bonus"),
        CheckConstraint(
            "expiry_days IS NULL OR expiry_days BETWEEN 1 AND 3650", name="ck_add_on_expiry"
        ),
        CheckConstraint(
            f"limit_field IS NULL OR {LIMIT_FIELD_CHECK}", name="ck_add_on_limit_field"
        ),
        CheckConstraint("limit_delta IS NULL OR limit_delta >= 1", name="ck_add_on_limit_delta"),
        CheckConstraint("questions IS NULL OR questions >= 1", name="ck_add_on_questions"),
        CheckConstraint(
            "duration IS NULL OR duration in ('period','permanent')", name="ck_add_on_duration"
        ),
        CheckConstraint(KIND_FIELDS_CHECK, name="ck_add_on_fields_match_kind"),
        # P5 plans
        CheckConstraint(
            "(kind in ('extra_limit','feature_unlock')) = (duration IS NOT NULL)",
            name="ck_add_on_duration_kind",
        ),
        CheckConstraint("feature IS NULL OR feature in ('byok')", name="ck_add_on_feature"),
        CheckConstraint(RENEWS_CHECK, name="ck_add_on_renews"),
        CheckConstraint(
            "includes_support_months IS NULL OR "
            "(kind = 'service' AND includes_support_months BETWEEN 1 AND 36)",
            name="ck_add_on_support_months",
        ),
        CheckConstraint(f"sac_code IS NULL OR sac_code ~ '{SAC_RE}'", name="ck_add_on_sac_code"),
        # /P5 plans
        CheckConstraint(
            "(kind = 'credit_pack') OR (bonus_pct = 0 AND expiry_days IS NULL)",
            name="ck_add_on_credit_only_fields",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    credit_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bonus_pct: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"), default=0
    )
    expiry_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limit_field: Mapped[str | None] = mapped_column(String(32), nullable=True)
    limit_delta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    questions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # P5 plans: `feature_unlock` names its feature; `service` may include months of support;
    # `sold_to_owners` puts it in the business owner's shop; `placeholder` marks a seeded price
    # the deployer has not set yet (editing the price clears it).
    feature: Mapped[str | None] = mapped_column(String(16), nullable=True)
    renews: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    includes_support_months: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    sac_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    sold_to_owners: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    placeholder: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    # /P5 plans
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class AddOnGrant(Base):
    __tablename__ = "add_on_grants"
    __table_args__ = (
        CheckConstraint(f"kind in ({KIND_LIST})", name="ck_add_on_grant_kind"),  # P5 plans
        # P1 payments
        CheckConstraint(
            "payment in ('wallet','external','free','gateway')", name="ck_add_on_grant_payment"
        ),
        # /P1 payments
        CheckConstraint(KIND_FIELDS_CHECK, name="ck_add_on_grant_fields_match_kind"),
        Index(
            "ix_add_on_grants_tenant_active",
            "tenant_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_add_on_grants_add_on", "add_on_id"),
        # P5 plans
        Index(
            "ix_add_on_grants_renewing",
            "ends_at",
            postgresql_where=text("renews AND revoked_at IS NULL AND cancelled_at IS NULL"),
        ),
        # /P5 plans
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_add_on_grants_tenant_id_tenants"),
        nullable=False,
    )
    add_on_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "add_on_catalogue.id", ondelete="RESTRICT", name="fk_add_on_grants_add_on_id_add_ons"
        ),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    credit_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bonus_pct: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0"), default=0
    )
    limit_field: Mapped[str | None] = mapped_column(String(32), nullable=True)
    limit_delta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    questions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # P5 plans: the same fields as the add-on, copied when granted
    feature: Mapped[str | None] = mapped_column(String(16), nullable=True)
    renews: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    includes_support_months: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    sac_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    renewed_through: Mapped[date | None] = mapped_column(Date, nullable=True)
    # /P5 plans
    payment: Mapped[str] = mapped_column(String(10), nullable=False)
    # P1 payments
    provider_payment_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # /P1 payments
    entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("wallet_ledger.id", ondelete="SET NULL", name="fk_add_on_grants_entry_id"),
        nullable=True,
    )
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    revoke_memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    granted_by: Mapped[str] = mapped_column(String(320), nullable=False)
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
