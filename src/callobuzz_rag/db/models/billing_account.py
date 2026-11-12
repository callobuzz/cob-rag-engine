"""A billing account: one per workspace, created lazily on first need (billing BIL2).

Holds the workspace's currency (locked once it has a transaction), the per-customer overrides of
its plan's pricing, limit overrides, the pause override, and the wallet cache columns the ledger
(BIL3) maintains: `balance_minor` is a cache of the sum of the ledger, written only by
`billing/ledger.py:append_entries`. Deleting the tenant deletes the account.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class BillingAccount(Base):
    __tablename__ = "billing_accounts"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_billing_accounts_tenant"),
        CheckConstraint("currency in ('INR','USD')", name="ck_billing_accounts_currency"),
        CheckConstraint(
            "pause_override in ('auto','never','force')", name="ck_billing_accounts_pause"
        ),
        CheckConstraint("billing_day BETWEEN 1 AND 28", name="ck_billing_accounts_day"),
        CheckConstraint(
            "usage_residue_micro BETWEEN 0 AND 9999", name="ck_billing_accounts_residue"
        ),
        # TV4 schema
        CheckConstraint(
            "spend_cap_minor IS NULL OR spend_cap_minor >= 0", name="ck_billing_accounts_spend_cap"
        ),
        Index(
            "ix_billing_accounts_pending_change_at",
            "pending_change_at",
            postgresql_where=text("pending_change_at IS NOT NULL"),
        ),
        # /TV4 schema
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE", name="fk_billing_accounts_tenant_id_tenants"),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    overrides: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    limit_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    pause_override: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'auto'"), default="auto"
    )
    billing_day: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1"), default=1
    )
    period_anchor: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_charge_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Cache of the ledger's sum (BIL3 writes it, only inside `append_entries`).
    balance_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), default=0
    )
    ledger_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), default=0
    )
    usage_residue_micro: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    alert_level: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_top_up_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0"), default=0
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # TV4 schema: a change that applies at the next period (pending_change), and the
    # owner's monthly spending cap in minor units (spend_cap_minor, NULL = none) (TV3)
    pending_change: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    pending_change_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    spend_cap_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # /TV4 schema
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
