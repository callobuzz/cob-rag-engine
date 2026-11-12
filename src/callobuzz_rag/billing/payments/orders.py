"""Quotes and checkout orders (P1): what the owner will pay, and the order the provider holds.

The owner types the amount **for the balance** (the taxable value) and sees "₹1,000 + ₹180 GST =
₹1,180 to pay". When the deployer's prices include GST (`invoicing.prices_include_gst`) the typed
amount is the total and the balance gets the taxable part. Nothing here moves money: that is
`capture.apply_captured`.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.settings_store import EngineSettings
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing import accounts
from callobuzz_rag.billing.invoicing import gst
from callobuzz_rag.billing.money import Money
from callobuzz_rag.billing.payments import profiles
from callobuzz_rag.billing.payments.base import PaymentProvider, ProviderError, Purpose
from callobuzz_rag.db.models.add_on import AddOn
from callobuzz_rag.db.models.payment import PaymentOrder

ORDER_LIFETIME = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class OrderQuote:
    """What the owner sees before paying."""

    purpose: Purpose
    taxable_minor: int  # what reaches the balance (top_up, plan) or the add-on's price
    tax: gst.TaxSplit
    gross_minor: int  # == tax.total_minor: what the provider charges
    currency: Literal["INR"]
    description: str
    sac_code: str
    add_on_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class QuoteContext:
    """Everything a quote needs, loaded once."""

    ctx: accounts.BillingContext
    profile: Any  # BillingProfile
    settings: EngineSettings


def _out_of_range(settings: EngineSettings, *, below: bool) -> APIError:
    limits = settings.payments
    low = Money(limits.min_top_up_minor, "INR").format()
    high = Money(limits.max_payment_minor, "INR").format()
    return APIError(
        422,
        f"The amount must be between {low} and {high}."
        if below
        else f"A single payment can be at most {high}. For more, pay in parts.",
        "amount_out_of_range",
        min_minor=limits.min_top_up_minor,
        max_minor=limits.max_payment_minor,
    )


async def load_quote_context(
    session: AsyncSession, tenant_id: uuid.UUID, settings: EngineSettings
) -> QuoteContext:
    """The workspace's billing context and tax details, or the 409 that says what is missing."""
    ctx = await accounts.load_context(session, tenant_id)
    if ctx.effective.exempt:
        raise APIError(
            409,
            "This workspace is never billed, so there is nothing to pay.",
            "billing_exempt",
        )
    if ctx.effective.currency != "INR":
        raise APIError(
            409,
            "Online payment is available in rupees only for now. Please contact us to pay.",
            "payments_currency_unsupported",
        )
    profile = await profiles.get_profile(session, tenant_id)
    if not profiles.is_complete(profile):
        raise APIError(
            409,
            "Please add your billing name and address first, so we can make your tax invoice.",
            "tax_details_required",
        )
    return QuoteContext(ctx, profile, settings)


def _split(price_minor: int, supply: profiles.Supply, *, inclusive: bool) -> gst.TaxSplit:
    if inclusive:
        return gst.split_inclusive(price_minor, supply.kind, supply.place_of_supply)
    return gst.split_exclusive(price_minor, supply.kind, supply.place_of_supply)


async def quote(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    settings: EngineSettings,
    *,
    purpose: Purpose,
    amount_minor: int | None,
    add_on_id: uuid.UUID | None,
    now: datetime | None = None,
) -> OrderQuote:
    """What `purpose` costs this workspace, GST included.

    Raises:
        APIError: 409 `billing_exempt`, `payments_currency_unsupported`, `tax_details_required`,
            `plan_has_no_fee`, `add_on_not_sellable`; 422 `amount_out_of_range`.
    """
    moment = now or datetime.now(UTC)
    held = await load_quote_context(session, tenant_id, settings)
    supply = profiles.supply_for(held.profile, settings, today=moment.date())
    inclusive = settings.invoicing.prices_include_gst
    limits = settings.payments
    sac = settings.invoicing.sac_default
    picked_add_on: uuid.UUID | None = None

    if purpose == "top_up":
        if amount_minor is None:
            raise APIError(
                422,
                "Please enter an amount.",
                "amount_out_of_range",
                min_minor=limits.min_top_up_minor,
                max_minor=limits.max_payment_minor,
            )
        split = _split(amount_minor, supply, inclusive=inclusive)
        if amount_minor < limits.min_top_up_minor:
            raise _out_of_range(settings, below=True)
        description = "Add money to your balance"
    elif purpose == "plan":
        fee = held.ctx.effective.fields.subscription_fee_minor
        if fee <= 0:
            raise APIError(
                409, "Your plan has no monthly fee, so there is nothing to pay.", "plan_has_no_fee"
            )
        # always exclusive: `subscriptions.run_due` charges the fee itself, so the top-up (the
        # taxable part) must equal it, whatever `prices_include_gst` says for other purposes
        split = _split(fee, supply, inclusive=False)
        description = f"{held.ctx.plan.name} plan, {moment:%b %Y}"
    else:
        if add_on_id is None:
            raise APIError(409, "That add-on is not available to buy.", "add_on_not_sellable")
        add_on = await session.get(AddOn, add_on_id)
        if add_on is None or not add_on.is_active or add_on.currency != "INR":
            raise APIError(409, "That add-on is not available to buy.", "add_on_not_sellable")
        split = _split(add_on.price_minor, supply, inclusive=inclusive)
        picked_add_on = add_on.id
        sac = add_on.sac_code or sac  # P5 plans: a service's own SAC is on its invoice
        if add_on.kind == "credit_pack" and add_on.credit_minor is not None:
            description = f"Credit pack {Money(add_on.credit_minor, 'INR').format()}"
        else:
            description = f"Add-on: {add_on.name}"
    if split.total_minor > limits.max_payment_minor:
        raise _out_of_range(settings, below=False)
    if split.total_minor < 100:  # the provider's smallest charge is ₹1
        raise _out_of_range(settings, below=True)
    return OrderQuote(
        purpose=purpose,
        taxable_minor=split.taxable_minor,
        tax=split,
        gross_minor=split.total_minor,
        currency="INR",
        description=description[:200],
        sac_code=sac,
        add_on_id=picked_add_on,
    )


async def _existing(session: AsyncSession, tenant_id: uuid.UUID, key: str) -> PaymentOrder | None:
    return (
        await session.execute(
            select(PaymentOrder).where(
                PaymentOrder.tenant_id == tenant_id, PaymentOrder.idempotency_key == key
            )
        )
    ).scalar_one_or_none()


def _same_request(order: PaymentOrder, q: OrderQuote) -> bool:
    return (
        order.purpose == q.purpose
        and order.add_on_id == q.add_on_id
        and order.gross_minor == q.gross_minor
        and order.taxable_minor == q.taxable_minor
    )


def _conflict(key: str) -> APIError:
    return APIError(
        409,
        "That request was already used for a different payment. Start a new one.",
        "idempotency_conflict",
        idempotency_key=key,
    )


def checkout_notes(order: PaymentOrder) -> dict[str, str]:
    return {"cob_order": str(order.id), "cob_account": str(order.account_id)}


async def start_order(
    session: AsyncSession,
    provider: PaymentProvider,
    tenant_id: uuid.UUID,
    settings: EngineSettings,
    *,
    purpose: Purpose,
    amount_minor: int | None,
    add_on_id: uuid.UUID | None,
    idempotency_key: str,
    created_by: str,
    channel: Literal["checkout", "link"] = "checkout",
    now: datetime | None = None,
) -> tuple[PaymentOrder, OrderQuote, bool]:
    """Create the order row and the provider's order (once per `(workspace, idempotency_key)`).

    Returns `(order, quote, created)`. The caller commits. A provider failure rolls the row back
    and raises 502 `payments_unavailable`; the same key may be used again.

    Raises:
        APIError: the quote's errors, 409 `idempotency_conflict`, 502 `payments_unavailable`.
    """
    moment = now or datetime.now(UTC)
    q = await quote(
        session,
        tenant_id,
        settings,
        purpose=purpose,
        amount_minor=amount_minor,
        add_on_id=add_on_id,
        now=moment,
    )
    existing = await _existing(session, tenant_id, idempotency_key)
    if existing is not None:
        if not _same_request(existing, q):
            raise _conflict(idempotency_key)
        return existing, q, False
    ctx = await accounts.load_context(session, tenant_id)
    order = PaymentOrder(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        account_id=ctx.account.id,
        purpose=q.purpose,
        add_on_id=q.add_on_id,
        channel=channel,
        currency=q.currency,
        taxable_minor=q.taxable_minor,
        cgst_minor=q.tax.cgst_minor,
        sgst_minor=q.tax.sgst_minor,
        igst_minor=q.tax.igst_minor,
        gross_minor=q.gross_minor,
        tax_kind=q.tax.kind,
        place_of_supply=q.tax.place_of_supply,
        description=q.description,
        sac_code=q.sac_code,
        provider=provider.provider,
        status="created",
        idempotency_key=idempotency_key,
        created_by=created_by[:320],
        created_at=moment,
        expires_at=moment + ORDER_LIFETIME,
    )
    try:
        async with session.begin_nested():
            session.add(order)
            await session.flush()
    except IntegrityError:  # a concurrent request with the same key won
        existing = await _existing(session, tenant_id, idempotency_key)
        if existing is None:
            raise
        if not _same_request(existing, q):
            raise _conflict(idempotency_key) from None
        return existing, q, False
    if channel == "checkout":
        try:
            provider_order = await provider.create_order(
                amount_minor=q.gross_minor,
                currency=q.currency,
                receipt=str(order.id),
                notes=checkout_notes(order),
            )
        except ProviderError as exc:
            await session.delete(order)
            await session.flush()
            raise APIError(
                502,
                "We could not reach the payment service. Nothing was charged. "
                "Please try again in a moment.",
                "payments_unavailable",
                reason=exc.kind,
            ) from None
        order.provider_order_id = provider_order.provider_order_id
        await session.flush()
    return order, q, True
