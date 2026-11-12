"""Payment links (P1): the operator sends a customer a page to pay, by e-mail or WhatsApp.

A link is an order with `channel="link"`: the provider makes its own order when the customer pays,
so the order row has no provider order id; the payment is matched to it by the note we put on the
link (`cob_order`). The same GST split, the same `apply_captured`, the same ledger entry as a
payment made inside the dashboard. A link stops working after `payments.link_expiry_days`.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.settings_store import EngineSettings
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing.payments import orders
from callobuzz_rag.billing.payments.base import PaymentProvider, ProviderError, Purpose
from callobuzz_rag.db.models.payment import PaymentOrder


async def create_link(
    session: AsyncSession,
    provider: PaymentProvider,
    tenant_id: uuid.UUID,
    settings: EngineSettings,
    *,
    purpose: Purpose,
    amount_minor: int | None,
    add_on_id: uuid.UUID | None,
    customer_name: str,
    customer_email: str | None,
    created_by: str,
    now: datetime | None = None,
) -> tuple[PaymentOrder, orders.OrderQuote]:
    """Make the order and the provider's payment link. The caller commits.

    Raises:
        APIError: the quote's errors (`tax_details_required`, `amount_out_of_range`, ...) and
            502 `payments_unavailable`; nothing is kept then.
    """
    moment = now or datetime.now(UTC)
    order, quote, _ = await orders.start_order(
        session,
        provider,
        tenant_id,
        settings,
        purpose=purpose,
        amount_minor=amount_minor,
        add_on_id=add_on_id,
        idempotency_key=f"link-{uuid.uuid4().hex}",
        created_by=created_by,
        channel="link",
        now=moment,
    )
    expires = moment + timedelta(days=settings.payments.link_expiry_days)
    customer: dict[str, str] = {"name": customer_name[:100]}
    if customer_email:
        customer["email"] = customer_email
    try:
        link_id, short_url = await provider.create_payment_link(
            amount_minor=order.gross_minor,
            currency=order.currency,
            reference_id=str(order.id),
            description=order.description,
            customer=customer,
            expire_by=expires,
            notes=orders.checkout_notes(order),
        )
    except ProviderError as exc:
        await session.delete(order)
        await session.flush()
        raise APIError(
            502,
            "We could not reach the payment service. No link was made. Please try again.",
            "payments_unavailable",
            reason=exc.kind,
        ) from None
    order.link_id = link_id
    order.short_url = short_url
    order.expires_at = expires
    await session.flush()
    return order, quote


def link_context(order: PaymentOrder, tenant_name: str, amount: str) -> dict[str, Any]:
    """The `payment_link` e-mail's context (minus `pay_url`, which the caller knows)."""
    assert order.expires_at is not None
    return {
        "tenant_name": tenant_name,
        "amount": amount,
        "purpose_label": order.description,
        "expires_on": f"{order.expires_at:%d %b %Y}",
    }
