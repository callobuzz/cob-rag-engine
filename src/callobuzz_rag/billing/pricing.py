"""How a plan charges, and what one customer actually pays after their overrides.

`PricingFields` is what a plan's pricing version holds. `PricingOverrides` is the same fields,
all optional: a customer's own terms, stored on their billing account. `combine` is the pure
rule (plan, then overrides, field by field); `effective_pricing` reads the rows in one query.

A plan without a pricing row behaves exactly like today: nothing charged, nobody paused.
Money is integer minor units (paise / cents); there is no float anywhere in an amount.
"""

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing.money import CURRENCIES, Currency
from callobuzz_rag.core.exceptions import RAGKernelError
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

UsageMode = Literal["included", "per_question", "cost_plus"]
ByokMode = Literal["free", "per_question"]
Overage = Literal["block", "charge", "allow"]
OverageMode = Literal["per_question", "cost_plus"]
PaymentType = Literal["prepaid", "postpaid"]
Period = Literal["monthly", "yearly"]

DEFAULT_THRESHOLDS = (20, 5, 0)

Big = Annotated[int, Field(ge=0, le=10**12)]
PerQuestion = Annotated[int, Field(ge=0, le=10**9)]


class TenantNotFoundError(RAGKernelError):
    """No workspace with that id."""


def check_thresholds(value: list[int] | None) -> list[int] | None:
    """Alert thresholds: percentages 0..100, strictly descending (so unique)."""
    if value is None:
        return None
    if any(type(v) is not int or not 0 <= v <= 100 for v in value):
        raise ValueError("thresholds are whole percentages from 0 to 100")
    if any(a <= b for a, b in pairwise(value)):
        raise ValueError("thresholds must be unique and in descending order")
    return value


class PricingFields(BaseModel):
    """One pricing version of a plan; money in minor units of `currency`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    currency: Currency = "INR"
    subscription_fee_minor: Big = 0
    billing_period: Period = "monthly"
    trial_days: int = Field(0, ge=0, le=365)
    usage_mode: UsageMode = "included"
    per_question_minor: PerQuestion = 0
    markup_bp: int = Field(0, ge=0, le=100_000)  # basis points: 2500 = 25 %
    byok_mode: ByokMode = "free"
    byok_per_question_minor: PerQuestion = 0
    byok_counts_toward_caps: bool = True
    overage: Overage = "block"
    overage_mode: OverageMode = "per_question"
    overage_per_question_minor: PerQuestion = 0
    payment_type: PaymentType = "prepaid"
    credit_limit_minor: Big = 0  # postpaid
    grace_minor: Big = 0  # prepaid
    included_credit_minor: Big = 0  # per billing period
    included_credit_expires: bool = True
    alert_thresholds_pct: list[int] = Field(
        default_factory=lambda: list(DEFAULT_THRESHOLDS), max_length=5
    )
    members_see_money: bool = False

    @field_validator("alert_thresholds_pct")
    @classmethod
    def _thresholds(cls, value: list[int]) -> list[int]:
        return check_thresholds(value) or []


class PricingOverrides(BaseModel):
    """A customer's own terms: every `PricingFields` field except `currency`, `None` = follow
    the plan."""

    model_config = ConfigDict(extra="forbid", strict=True)

    subscription_fee_minor: Big | None = None
    billing_period: Period | None = None
    trial_days: int | None = Field(None, ge=0, le=365)
    usage_mode: UsageMode | None = None
    per_question_minor: PerQuestion | None = None
    markup_bp: int | None = Field(None, ge=0, le=100_000)
    byok_mode: ByokMode | None = None
    byok_per_question_minor: PerQuestion | None = None
    byok_counts_toward_caps: bool | None = None
    overage: Overage | None = None
    overage_mode: OverageMode | None = None
    overage_per_question_minor: PerQuestion | None = None
    payment_type: PaymentType | None = None
    credit_limit_minor: Big | None = None
    grace_minor: Big | None = None
    included_credit_minor: Big | None = None
    included_credit_expires: bool | None = None
    alert_thresholds_pct: list[int] | None = Field(None, max_length=5)
    members_see_money: bool | None = None

    @field_validator("alert_thresholds_pct")
    @classmethod
    def _thresholds(cls, value: list[int] | None) -> list[int] | None:
        return check_thresholds(value)

    def set_fields(self) -> dict[str, Any]:
        """The fields that carry an override (not None)."""
        return {k: v for k, v in self if v is not None}


def overrides_from_stored(stored: Mapping[str, Any] | None) -> PricingOverrides:
    """Re-validate stored overrides field by field. A key that no longer validates (a rule
    tightened since, a removed field) is ignored with a warning, never an error: one stale key
    must not stop a customer's answers."""
    good: dict[str, Any] = {}
    for key, value in (stored or {}).items():
        if value is None:
            continue
        try:
            PricingOverrides.model_validate({key: value})
        except ValidationError:
            logger.warning("billing: ignoring invalid stored override %r", key)
            continue
        good[key] = value
    return PricingOverrides.model_validate(good)


@dataclass(frozen=True)
class EffectivePricing:
    fields: PricingFields  # the plan's current version, then the account's overrides
    currency: Currency  # the account's (locked once the ledger has an entry)
    pricing_id: uuid.UUID | None  # the plan_pricing row in force (None: no pricing)
    exempt: bool  # operator workspace, system tenant or Internal plan: never charged or paused
    overridden: frozenset[str]  # the fields the account overrides

    def chargeable(self, payer: str) -> bool:
        """Whether usage by `payer` (`operator` key or `byok`) is billed at all."""
        if self.exempt:
            return False
        f = self.fields
        if payer == "operator":
            return (
                f.usage_mode != "included" or f.overage == "charge" or f.subscription_fee_minor > 0
            )
        if payer == "byok":
            return f.byok_mode == "per_question"
        return False


def combine(
    plan: PricingFields,
    overrides: PricingOverrides,
    *,
    currency: Currency,
    exempt: bool,
    pricing_id: uuid.UUID | None = None,
) -> EffectivePricing:
    """Plan fields with the customer's overrides on top, re-validated as a whole."""
    changes = overrides.set_fields()
    merged = {**plan.model_dump(), **changes, "currency": currency}
    return EffectivePricing(
        fields=PricingFields.model_validate(merged),
        currency=currency,
        pricing_id=pricing_id,
        exempt=exempt,
        overridden=frozenset(changes),
    )


def fields_from_row(row: PlanPricing) -> PricingFields:
    """A stored version as `PricingFields`."""
    return PricingFields.model_validate(
        {name: getattr(row, name) for name in PricingFields.model_fields}
    )


async def effective_pricing(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    at: datetime | None = None,
    now: datetime | None = None,
) -> EffectivePricing:
    """What this workspace pays now. One query: tenant, plan, the plan's current pricing and
    the workspace's billing account (either of the last two may be absent). Not cached, so a
    price change applies to the next answer.

    TV3 notice: a price change is announced ahead and applies from each workspace's next
    period, so the plan's current version is only used when it was already in force when this
    workspace's current period began; otherwise one more query finds the version that was.
    `now` is the clock for tests. `at` asks for the period starting then (the subscription
    schedule charges each period at
    its own price).

    Raises:
        TenantNotFoundError: no such workspace.
    """
    stmt = (
        select(Tenant, Plan, PlanPricing, BillingAccount)
        .select_from(Tenant)
        .join(Plan, Plan.id == Tenant.plan_id)
        .outerjoin(
            PlanPricing, and_(PlanPricing.plan_id == Plan.id, PlanPricing.is_current.is_(True))
        )
        .outerjoin(BillingAccount, BillingAccount.tenant_id == Tenant.id)
        .where(Tenant.id == tenant_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        raise TenantNotFoundError(f"Workspace {tenant_id} not found.")
    tenant, plan, pricing, account = row
    # TV3 notice
    chosen: PlanPricing | None = pricing
    if pricing is not None and pricing.effective_at is not None:
        from callobuzz_rag.billing import notice

        period = ((account.overrides or {}) if account is not None else {}).get(
            "billing_period"
        ) or pricing.billing_period
        now = now or datetime.now(UTC)
        start = (
            at
            if at is not None
            else await notice.account_period_start(
                session, plan.id, account, now, billing_period=str(period)
            )
        )
        if pricing.effective_at > start:
            chosen = await notice.pricing_in_force(session, plan.id, account, now, at=start)
    # /TV3 notice
    default_currency: Currency | None = None
    if account is None and chosen is None:
        from callobuzz_rag.accounts.settings_store import read_engine_settings_row

        default_currency = (await read_engine_settings_row(session))[0].billing.default_currency
    return build_effective(tenant, plan, chosen, account, default_currency=default_currency)


def build_effective(
    tenant: Tenant,
    plan: Plan,
    pricing: PlanPricing | None,
    account: BillingAccount | None,
    *,
    default_currency: Currency | None = None,
) -> EffectivePricing:
    """The pure part of `effective_pricing`, for callers that already hold the rows.

    The currency is the account's, else the pricing's, else `default_currency` (INR when the
    caller has none: only a workspace with neither an account nor pricing gets there, and
    then nothing is charged anyway)."""
    exempt = bool(tenant.is_operator or tenant.is_system or plan.is_internal)
    fields = fields_from_row(pricing) if pricing is not None else PricingFields()
    if account is not None and (account.ledger_seq > 0 or pricing is None):
        currency = cast("Currency", account.currency)
    elif pricing is not None:
        # Until the first transaction the plan's pricing currency is the truth, so a stale
        # account currency can never reinterpret the plan's minor-unit amounts.
        currency = cast("Currency", pricing.currency)
    else:
        currency = default_currency or "INR"
    return combine(
        fields,
        overrides_from_stored(account.overrides if account is not None else None),
        currency=currency,
        exempt=exempt,
        pricing_id=pricing.id if pricing is not None else None,
    )


# ---------------------------------------------------------------- plan pricing versions


def changed_fields(old: PricingFields, new: PricingFields) -> list[str]:
    """The fields whose value differs between two versions, in declaration order."""
    before, after = old.model_dump(), new.model_dump()
    return [name for name in PricingFields.model_fields if before[name] != after[name]]


async def current_pricing(session: AsyncSession, plan_id: uuid.UUID) -> PlanPricing | None:
    return (
        await session.execute(
            select(PlanPricing).where(
                PlanPricing.plan_id == plan_id, PlanPricing.is_current.is_(True)
            )
        )
    ).scalar_one_or_none()


async def pricing_history(session: AsyncSession, plan_id: uuid.UUID) -> list[PlanPricing]:
    """Every version of a plan's pricing, newest first (read-only history)."""
    rows = await session.execute(
        select(PlanPricing)
        .where(PlanPricing.plan_id == plan_id)
        .order_by(PlanPricing.version.desc())
    )
    return list(rows.scalars().all())


async def save_pricing(
    session: AsyncSession,
    plan: Plan,
    fields: PricingFields,
    *,
    created_by: str,
    effective_at: datetime | None = None,  # TV3 notice
) -> tuple[PlanPricing, list[str]]:
    """Insert the next version of `plan`'s pricing and make it current, in the caller's
    transaction (the plan row is locked, so two saves cannot take the same version).

    With `effective_at` (TV3 notice, `billing/notice.py` decides when that is allowed) the
    version is only *scheduled*: it is stored not current, and the hourly beat makes it current
    once `effective_at` has passed. Nothing about the current version or the accounts changes.

    Returns the new row and the fields that changed from the previous current version (or from
    the defaults when the plan had none). Does not commit.

    Raises:
        APIError 409 `pricing_locked` (the Internal plan), `currency_mismatch` (a workspace on
        the plan has an account with transactions in the other currency).
    """
    if plan.is_internal:
        raise pricing_locked()
    await session.execute(select(Plan.id).where(Plan.id == plan.id).with_for_update())
    previous = await current_pricing(session, plan.id)
    accounts = await lock_accounts_for_currency(session, plan, fields)
    scheduled = effective_at is not None  # TV3 notice
    if not scheduled:
        for account in accounts:
            if account.ledger_seq == 0 and account.currency != fields.currency:
                account.currency = fields.currency
    old = (
        fields_from_row(previous)
        if previous is not None
        else PricingFields(currency=fields.currency)
    )
    changed = changed_fields(old, fields)
    version = 1
    if previous is not None:
        version = previous.version + 1
        if not scheduled:
            await session.execute(
                update(PlanPricing).where(PlanPricing.id == previous.id).values(is_current=False)
            )
            await session.flush()
    row = PlanPricing(
        plan_id=plan.id,
        version=version,
        is_current=not scheduled,
        effective_at=effective_at,  # TV3 notice
        created_by=created_by[:320],
        **fields.model_dump(),
    )
    session.add(row)
    await session.flush()
    return row, changed


async def lock_accounts_for_currency(
    session: AsyncSession, plan: Plan, fields: PricingFields
) -> list[BillingAccount]:
    """Lock the plan's billing accounts and refuse a currency switch that one of them has
    already transacted in (409 `currency_mismatch`). Returns the locked accounts."""
    # Lock the plan's accounts first: the ledger writer locks the same rows, so an account
    # cannot take its first transaction between the check and the switch.
    accounts = (
        (
            await session.execute(
                select(BillingAccount)
                .join(Tenant, Tenant.id == BillingAccount.tenant_id)
                .where(Tenant.plan_id == plan.id)
                .with_for_update(of=BillingAccount)
            )
        )
        .scalars()
        .all()
    )
    from callobuzz_rag.billing.ledger import currency_frozen

    stuck = [
        a.tenant_id
        for a in accounts
        if a.currency != fields.currency and await currency_frozen(session, a)
    ][:5]
    if stuck:
        raise APIError(
            409,
            "Customers on this plan already have transactions (or usage not yet posted) in "
            "another currency, "
            "so its pricing cannot switch currency.",
            "currency_mismatch",
            tenant_ids=[str(t) for t in stuck],
            account_currency=next(c for c in CURRENCIES if c != fields.currency),
            currency=fields.currency,
        )
    return list(accounts)


def pricing_locked() -> APIError:
    return APIError(
        409,
        "The Internal plan is your own workspace's and is never billed, so it has no pricing.",
        "pricing_locked",
    )


async def check_plan_currency(
    session: AsyncSession, tenant_id: uuid.UUID, plan_id: uuid.UUID
) -> None:
    """Refuse putting a workspace on a plan priced in another currency than its locked account.

    An account is locked once it has a transaction. A plan without pricing, and a workspace
    without an account or with an empty ledger, are always fine.

    Raises:
        APIError 409 `currency_mismatch` (extras `account_currency`, `currency`).
    """
    account = (
        await session.execute(
            select(BillingAccount).where(BillingAccount.tenant_id == tenant_id).with_for_update()
        )
    ).scalar_one_or_none()
    if account is None:
        return
    row = await current_pricing(session, plan_id)
    if row is None:
        return
    from callobuzz_rag.billing.ledger import currency_frozen

    if not await currency_frozen(session, account):
        # Nothing posted yet: the new plan's currency simply becomes the account's.
        if account.currency != row.currency:
            account.currency = row.currency
        return
    if row.currency != account.currency:
        raise APIError(
            409,
            f"This workspace's balance is in {account.currency}, but that plan is priced in "
            f"{row.currency}. Move it to a plan priced in {account.currency}.",
            "currency_mismatch",
            account_currency=account.currency,
            currency=row.currency,
        )
