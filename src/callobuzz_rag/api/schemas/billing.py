"""Request and response bodies for billing pricing and accounts (BIL2).

Money is always an integer in minor units (paise / cents) next to a `currency`; no body carries a
float amount.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from callobuzz_rag.api.schemas.billing_addons import ActiveAddOn  # BIL5 add-ons
from callobuzz_rag.api.schemas.billing_shop import OwnKeyStatus  # P5 plans
from callobuzz_rag.billing.money import Currency
from callobuzz_rag.billing.pricing import (
    ByokMode,
    Overage,
    PaymentType,
    Period,
    PricingFields,
    UsageMode,
)
from callobuzz_rag.core.schemas import StrictRequest

PauseOverride = Literal["auto", "never", "force"]
# BIL4 state
PauseReason = Literal["prepaid_empty", "credit_limit", "forced"]
AccountStatus = Literal["active", "low", "paused", "forced", "not_billed"]
# /BIL4 state


class PlanPricingRequest(PricingFields, StrictRequest):
    """PUT = full replacement: an omitted field takes its `PricingFields` default (the dialog
    always sends every field), so a version row is always complete."""

    # TV3 notice: a change that costs a business more is announced first and starts at a later
    # date; `apply="now"` is for changes that cost nobody more.
    apply: Literal["now", "scheduled"] = "now"
    effective_at: datetime | None = Field(
        None,
        strict=False,
        description=(
            "When a scheduled change starts (an Indian midnight; a time of day is rounded up). "
            "Defaults to the earliest allowed date."
        ),
    )
    # /TV3 notice


class PlanPricingOut(PricingFields):
    """A plan's pricing version. `version` is null (and `is_current` false) for a plan that has
    none: the fields are then the defaults, under which nothing is charged."""

    plan_id: uuid.UUID
    version: int | None = None
    is_current: bool = False
    locked: bool = False  # the Internal plan: never billed, cannot be priced
    changed: list[str] = Field(default_factory=list)  # fields that changed (a PUT's response)
    created_at: datetime | None = None
    created_by: str | None = None
    # TV3 notice
    effective_at: datetime | None = None  # null = in force from creation
    scheduled: "PlanPricingOut | None" = None  # the change announced and not yet in force
    impact: Literal["lower", "same", "higher", "mixed"] | None = None  # a PUT's response
    workspaces: int | None = None  # businesses the change reaches (a PUT's response)
    # /TV3 notice


class PricingPreviewOut(BaseModel):
    """What saving a pricing would mean, without saving it."""

    impact: Literal["lower", "same", "higher", "mixed"]
    changed: list[str]
    workspaces: int
    earliest_effective_at: datetime


class PlanPricingHistoryResponse(BaseModel):
    items: list[PlanPricingOut]


class PendingChangeOut(BaseModel):
    """A raise for this workspace that waits for its notice period (TV3). `overrides` holds the
    requested terms (a value sets, null removes), `plan` the plan it is moving to; `changes` lists
    what the business will pay differently."""

    effective_at: datetime
    plan: "PlanRef | None" = None
    overrides: dict[str, Any] | None = None
    changes: list[dict[str, Any]] = Field(default_factory=list)


class PlanRef(BaseModel):
    id: uuid.UUID
    name: str


PendingChangeOut.model_rebuild()  # TV3 notice: PlanRef is defined just above


class UpdateBillingAccountRequest(StrictRequest):
    overrides: dict[str, Any] | None = Field(
        None,
        description=(
            "The customer's own terms: any pricing field except currency. A value sets it; "
            "null removes that override so the plan's value applies again."
        ),
    )
    limit_overrides: dict[str, int | None] | None = Field(
        None,
        description=(
            "Per-customer limits: an integer >= 1 sets it, null means unlimited. Send the "
            "whole object; it replaces the stored one."
        ),
    )
    currency: Currency | None = None
    pause_override: PauseOverride | None = None
    billing_day: int | None = Field(None, ge=1, le=28)
    notes: str | None = Field(None, max_length=2000)
    # TV3 notice: a raise waits for the notice period unless the business asked for it
    business_agreed: bool = Field(
        False,
        description=(
            "Apply a raise now because the business asked for it. Needs `reason`; audited."
        ),
    )
    reason: str | None = Field(None, max_length=500)
    # /TV3 notice


class BillingAccountOut(BaseModel):
    """Everything the super admin sees about one customer's billing."""

    tenant_id: uuid.UUID
    tenant_name: str
    plan: PlanRef
    currency: Currency
    exempt: bool
    pricing_version: int | None
    effective: PricingFields  # the plan's pricing with the overrides on top
    overrides: dict[str, Any]  # only what the admin set
    overridden: list[str]
    limit_overrides: dict[str, int | None]
    pause_override: PauseOverride
    billing_day: int
    period_anchor: date | None
    next_charge_on: date | None
    trial_ends_at: datetime | None
    balance_minor: int
    ledger_seq: int
    currency_locked: bool
    notes: str | None
    created_at: datetime | None
    updated_at: datetime | None
    # BIL4 state: what the balance hook last stored (a minute behind at most)
    status: AccountStatus = "active"
    pause_reason: PauseReason | None = None
    alert_level: int | None = None  # the lowest low-balance level already alerted (percent)
    pending_change: PendingChangeOut | None = None  # TV3 notice
    # /BIL4 state


class BillingAccountRow(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    plan: PlanRef
    currency: Currency
    exempt: bool
    payment_type: PaymentType
    balance_minor: int
    pause_override: PauseOverride
    overrides_count: int
    has_account: bool  # false: no account yet (created on first open)
    status: AccountStatus = "active"  # BIL4 state


class BillingAccountListResponse(BaseModel):
    items: list[BillingAccountRow]
    total: int


# BIL6 summary
class UsagePeriodOut(BaseModel):
    """This month so far. The amount and the count of unpriced AI calls are null for a member
    unless the plan lets members see money."""

    questions: int
    charged_minor: int | None = None
    unpriced: int | None = None


class StatementRef(BaseModel):
    month: str = Field(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    closing_minor: int
    live: bool


class BillingMeterOut(BaseModel):
    """The header's one-line balance (prepaid) or credit (postpaid), for a billed workspace.
    Amounts are null for a member unless the plan lets members see money."""

    payment_type: PaymentType
    currency: Currency
    money_visible: bool
    available_minor: int | None = None
    credit_limit_minor: int | None = None  # postpaid only
    paused: bool = False
    alert_level: int | None = None


# /BIL6 summary


# TV3 summary
SpendCapState = Literal["ok", "near", "reached"]


class RenewalOut(BaseModel):
    """The next renewal: the fee and included credit of the price in force that day."""

    on: date
    amount_minor: int
    included_credit_minor: int
    new_price_from: date | None = None  # set when a coming price starts with this renewal


class PriceChangeLine(BaseModel):
    label: str
    old_minor: int
    new_minor: int


class PriceChangeOut(BaseModel):
    """A price change that was announced and has not reached this workspace yet."""

    effective_on: date  # the first renewal this workspace pays it
    plan_name: str
    changes: list[PriceChangeLine]


class SetSpendCapRequest(StrictRequest):
    """The owner's monthly cap on usage charges, in minor units (paise, cents); null removes it."""

    cap_minor: Annotated[int, Field(strict=True, ge=1, le=10**12)] | None = None


class SpendCapOut(BaseModel):
    """The owner's monthly spending cap on usage. `max_minor` is the provider's ceiling."""

    cap_minor: int | None
    used_minor: int
    max_minor: int | None = None
    state: SpendCapState = "ok"


# /TV3 summary


# P2 invoices
class GstDisplay(BaseModel):
    """What the dashboard needs to show the GST-inclusive total next to a price."""

    registered: bool = Field(..., description="Whether the seller charges GST (has a GST number).")
    prices_include_gst: bool = Field(
        ..., description="True when the listed prices already include GST."
    )


# /P2 invoices


class BillingAccountView(BaseModel):
    """What a workspace owner sees (`GET /api/v1/billing/account`). Money fields are null for a
    member session unless the plan sets `members_see_money`. The markup, the operator's AI cost
    and the admin notes never appear."""

    tenant_id: uuid.UUID
    plan: PlanRef
    currency: Currency
    money_visible: bool
    payment_type: PaymentType
    usage_mode: UsageMode
    byok_mode: ByokMode
    subscription_fee_minor: int | None
    billing_period: Period
    per_question_minor: int | None
    byok_per_question_minor: int | None
    overage: Overage
    overage_per_question_minor: int | None
    included_credit_minor: int | None
    credit_limit_minor: int | None
    exempt: bool
    # BIL3 ledger
    balance_minor: int | None = None  # null for a member unless the plan allows it
    available_minor: int | None = None  # the balance minus usage not yet posted
    # /BIL3 ledger
    # BIL4 state: answers are paused for balance (the banner), why, and the low-balance level
    paused: bool = False
    pause_reason: PauseReason | None = None
    alert_level: int | None = None  # 20 / 5 / ... the lowest threshold reached; null above all
    # /BIL4 state
    # BIL5 add-ons
    add_ons: list[ActiveAddOn] = Field(default_factory=list)  # in force now, no prices
    # /BIL5 add-ons
    # BIL6 summary
    billed: bool = False  # false: this workspace's plan charges nothing (no balance to show)
    usage_this_period: UsagePeriodOut | None = None
    next_charge_on: date | None = None  # the next subscription charge; null without money rights
    trial_ends_at: datetime | None = None
    statements: list[StatementRef] = Field(default_factory=list)  # owners only, newest first
    # /BIL6 summary
    # TV3 summary: no surprises (null for a member without money rights, and not billed)
    gst: GstDisplay | None = None  # P2 invoices
    renewal: RenewalOut | None = None
    projected_month_end_minor: int | None = None
    price_changes: list[PriceChangeOut] = Field(default_factory=list)
    spend_cap: SpendCapOut | None = None
    # /TV3 summary
    # P5 plans: may this workspace use its own AI key, and is one saved
    byok: OwnKeyStatus | None = None
    # /P5 plans
