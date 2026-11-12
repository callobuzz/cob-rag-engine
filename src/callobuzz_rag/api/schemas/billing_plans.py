"""Bodies for self-service plan choice (P6): the owner's plan catalogue, a plan change and the
public list the sign-up page shows.

Money is integer minor units next to a currency, with the GST-inclusive total beside every fee
(no drip pricing). Nothing here carries the deployer's costs, markup, credit limit or grace.
"""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from callobuzz_rag.billing.money import Currency
from callobuzz_rag.core.schemas import StrictRequest

Direction = Literal["current", "upgrade", "downgrade", "switch"]
ChangeStatus = Literal["applied", "awaiting_payment", "scheduled", "current"]


class PlanIncludes(BaseModel):
    """What a plan includes; null = unlimited."""

    max_knowledge_bases: int | None
    monthly_questions: int | None
    max_documents: int | None
    max_widget_keys: int | None
    own_ai_key: bool = Field(description="Whether the plan includes using your own AI key.")


class PlanPrice(BaseModel):
    currency: Currency
    subscription_fee_minor: int = Field(description="Per billing period, before GST.")
    inclusive_total_minor: int = Field(description="The fee with GST, when GST applies.")
    billing_period: Literal["monthly", "yearly"]
    included_credit_minor: int = Field(description="Credit included every period.")
    usage_mode: str
    per_question_minor: int


class PlanChoice(BaseModel):
    id: uuid.UUID
    name: str
    slug: str = Field(description="The name in a sign-up link: `/signup?plan=<slug>`.")
    description: str | None
    current: bool
    direction: Direction = Field(
        description="`upgrade` applies now (the difference for the rest of the period is "
        "charged), `downgrade` starts at the next renewal, `switch` applies now at no charge."
    )
    price: PlanPrice | None = Field(
        None, description="Null while billing is off: the plan is its limits."
    )
    includes: PlanIncludes
    due_now_minor: int = Field(
        0, description="Taken from the balance if you move now (upgrades), before GST."
    )
    credit_now_minor: int = Field(0, description="Included credit added with it.")
    first_period: bool = Field(
        False, description="True when this pays the plan's first full period (no schedule yet)."
    )
    effective_on: date | None = Field(None, description="When the change starts.")
    affordable: bool = Field(True, description="False: the balance cannot cover it yet.")
    available: bool = Field(True, description="False: it cannot be chosen here (see reason).")
    unavailable_reason: str | None = Field(
        None, description="`needs_billing` (a fee with payments off), `price_not_set`."
    )


class PendingPlanChange(BaseModel):
    kind: Literal["scheduled", "awaiting_payment"]
    plan_id: uuid.UUID
    plan_name: str
    effective_on: date | None = Field(None, description="A scheduled move: its day.")
    amount_due_minor: int = Field(0, description="Waiting for payment: what will be charged.")
    shortfall_minor: int = Field(0, description="What the balance still needs.")
    pay_amount_minor: int = Field(0, description="What to pay on the pay page (whole units).")
    expires_at: datetime | None = Field(None, description="When a waiting change lapses.")
    by_provider: bool = Field(
        False, description="True: the provider scheduled it; only they can change it."
    )


class PlanChoiceList(BaseModel):
    billing_on: bool
    currency: Currency
    current_plan_id: uuid.UUID
    can_change: bool = Field(description="False: an exempt workspace, or a member.")
    pay_online: bool = Field(description="Online payment is available for a waiting change.")
    renews_on: date | None = Field(
        None, description="The next renewal: an upgrade is charged until then."
    )
    pending: PendingPlanChange | None = None
    items: list[PlanChoice]


class ChangePlanRequest(StrictRequest):
    plan_id: uuid.UUID


class ChangePlanResponse(BaseModel):
    status: ChangeStatus = Field(
        description="`applied` (now), `scheduled` (at the renewal), `awaiting_payment` (add "
        "money, then it applies), `current` (already on it; nothing changed)."
    )
    plan_id: uuid.UUID
    plan_name: str
    charged_minor: int = 0
    credit_minor: int = 0
    effective_on: date | None = None
    amount_due_minor: int = 0
    shortfall_minor: int = 0
    pay_amount_minor: int = 0
    pay_online: bool = False
    expires_at: datetime | None = None


class PublicPlan(BaseModel):
    """A plan a visitor can choose at sign-up."""

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    free: bool = Field(description="True: no fee (nothing to pay to start).")
    price: PlanPrice | None = Field(None, description="Null while billing is off.")


class PublicPlanList(BaseModel):
    items: list[PublicPlan]
