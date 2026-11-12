"""Bodies for the wallet ledger routes (BIL3): the super admin's transactions list and manual
entries, the owner's own list, and the reconciliation runs.

Money is an integer in minor units next to a `currency`. The customer's view carries only the
customer-safe description (`description`), never the admin's memo or who made the entry.
"""

import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from callobuzz_rag.billing.money import Currency
from callobuzz_rag.core.schemas import StrictRequest

EntryKind = Literal[
    "top_up",
    "subscription_charge",
    "usage_charge",
    "add_on_purchase",
    "adjustment",
    "refund",
    "included_credit",
    "expiry",
]


class ManualEntryRequest(StrictRequest):
    """A top-up, a correction or a refund the super admin records by hand."""

    entry_type: Literal["top_up", "adjustment", "refund"]
    amount_minor: int = Field(
        ...,
        ge=-(10**12),
        le=10**12,
        description=(
            "Top-up and refund: a positive amount (a refund is stored as money going out). "
            "Adjustment: a signed amount, never zero."
        ),
    )
    memo: str | None = Field(
        None, max_length=500, description="Admin-only note. Required for an adjustment or refund."
    )
    memo_public: str | None = Field(
        None, max_length=200, description="What the customer sees. A default fits each type."
    )
    idempotency_key: str = Field(
        ...,
        pattern=r"^[A-Za-z0-9_-]{8,64}$",
        description="One per dialog open: sending the same key again changes nothing.",
    )
    confirm_amount_minor: int | None = Field(
        None, description="The amount typed again; required at or above the confirmation limit."
    )
    expires_at: datetime | None = Field(
        None, description="Positive adjustments only: when this goodwill credit expires."
    )

    @model_validator(mode="after")
    def _fits_together(self) -> "ManualEntryRequest":
        if self.amount_minor == 0:
            raise ValueError("The amount cannot be zero.")
        if self.entry_type in ("top_up", "refund") and self.amount_minor < 0:
            raise ValueError("Enter a positive amount; a refund is recorded as money going out.")
        if self.entry_type in ("adjustment", "refund") and not (self.memo or "").strip():
            raise ValueError("A note is required for an adjustment or a refund.")
        if self.expires_at is not None:
            if self.entry_type != "adjustment" or self.amount_minor < 0:
                raise ValueError("Only a positive adjustment can expire.")
            if self.expires_at.tzinfo is None:
                raise ValueError("The expiry needs a time zone.")
            if self.expires_at <= datetime.now(UTC):
                raise ValueError("The expiry must be in the future.")
        return self


class LedgerEntryOut(BaseModel):
    """One transaction, as the super admin sees it."""

    id: uuid.UUID
    seq: int
    entry_type: EntryKind
    amount_minor: int
    currency: Currency
    balance_after_minor: int
    memo: str | None
    memo_public: str | None
    reference_type: str | None
    reference_id: str | None
    idempotency_key: str
    period: date
    actor_label: str
    created_at: datetime


class LedgerListResponse(BaseModel):
    items: list[LedgerEntryOut]
    total: int
    balance_minor: int
    available_minor: int  # the balance minus usage not yet posted


class LedgerEntryView(BaseModel):
    """One transaction, as the workspace owner sees it."""

    id: uuid.UUID
    entry_type: EntryKind
    amount_minor: int
    currency: Currency
    balance_after_minor: int
    description: str
    created_at: datetime


class LedgerViewListResponse(BaseModel):
    items: list[LedgerEntryView]
    total: int


class ReconciliationFinding(BaseModel):
    check: str
    account_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    informational: bool = False


class ReconciliationOut(BaseModel):
    id: uuid.UUID
    ran_at: datetime
    day: date
    ok: bool
    checked_accounts: int
    findings: list[ReconciliationFinding]
    duration_ms: int


class ReconciliationListResponse(BaseModel):
    items: list[ReconciliationOut]


class ReconciliationQueued(BaseModel):
    queued: bool = True
