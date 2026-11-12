"""A workspace pays: tax details, quotes, checkout orders and its payment list (P1).

`/api/v1/billing/tax-details`, `/payments/*`. Owners only (money): a member is refused with 403
`owner_required`, a knowledge-base key with 403 `scope_forbidden`, the admin principal with 403
`no_workspace`. Every route answers 404 `billing_disabled` while billing is off, and the payment
routes 404 `payments_disabled` while no provider is configured. The rules live in
`billing/payments/`.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.accounts.settings_store import read_engine_settings_row
from callobuzz_rag.api.deps import get_session
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.routes.billing_statements import owner_workspace
from callobuzz_rag.api.schemas.payments import (
    CreateOrderRequest,
    OrderOut,
    OrderQuoteOut,
    OrderQuoteRequest,
    PaymentConfigOut,
    PaymentView,
    PaymentViewList,
    TaxDetailsOut,
    TaxDetailsRequest,
    VerifyPaymentOut,
    VerifyPaymentRequest,
)
from callobuzz_rag.billing import accounts, ledger
from callobuzz_rag.billing.byok import byok_status  # P5 plans
from callobuzz_rag.billing.config import require_billing_enabled
from callobuzz_rag.billing.payments import factory, orders, profiles
from callobuzz_rag.billing.payments import settings as payment_settings
from callobuzz_rag.billing.payments.base import PaymentProvider, ProviderError, ProviderOrder
from callobuzz_rag.billing.payments.capture import apply_captured, run_after_commit
from callobuzz_rag.db.models.add_on import AddOn  # P5 plans
from callobuzz_rag.db.models.payment import Payment, PaymentOrder
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

# P1 payments
router = APIRouter(
    prefix="/api/v1/billing", tags=["payments"], dependencies=[Depends(require_billing_enabled)]
)


@router.get("/tax-details", response_model=TaxDetailsOut)
async def get_tax_details(
    request: Request, session: AsyncSession = Depends(get_session)
) -> TaxDetailsOut:
    """Who your tax invoices are made out to. Empty until you save them."""
    tenant_id = owner_workspace(request)
    return profiles.to_out(await profiles.get_profile(session, tenant_id))


@router.put("/tax-details", response_model=TaxDetailsOut)
async def put_tax_details(
    body: TaxDetailsRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> TaxDetailsOut:
    """Save the name, address and state your tax invoices carry. A GST number is optional; when
    you give one, its state decides the tax split. Owners only."""
    tenant_id = owner_workspace(request)
    row, changed = await profiles.save_profile(session, tenant_id, body)
    if changed:
        tenant = await session.get(Tenant, tenant_id)
        await record_audit(
            session,
            request,
            "billing.tax_details_updated",
            tenant_id=tenant_id,
            tenant_label=tenant.name if tenant is not None else None,
            target_type="billing_profile",
            target_id=str(tenant_id),
            details={"changed": changed, "gstin_set": row.gstin is not None},
        )
        await session.commit()
    return profiles.to_out(row)


async def _provider(request: Request, session: AsyncSession) -> PaymentProvider:
    provider = await factory.provider_for(request.app, session)
    if provider is None:
        raise APIError(404, "Online payments are not switched on here.", "payments_disabled")
    return provider


# P5 plans
async def _owner_may_buy(
    session: AsyncSession, tenant_id: uuid.UUID, purpose: str, add_on_id: uuid.UUID | None
) -> None:
    """An owner pays online only for what the deployer put in the shop (`sold_to_owners`), and
    never for a feature the plan or an active add-on already gives. The operator's own payment
    links (`payments_admin`) are not held to the shop.

    Raises:
        APIError 409 `add_on_not_sellable`, `feature_already_included`.
    """
    if purpose != "add_on" or add_on_id is None:
        return
    add_on = await session.get(AddOn, add_on_id)
    if add_on is None or not add_on.is_active or not add_on.sold_to_owners or add_on.placeholder:
        raise APIError(409, "That add-on is not available to buy.", "add_on_not_sellable")
    if add_on.kind == "feature_unlock":
        allowed, _source = await byok_status(session, tenant_id)
        if allowed:
            raise APIError(
                409,
                "Your plan already includes this, so there is nothing to buy.",
                "feature_already_included",
            )


# /P5 plans
def _quote_out(q: orders.OrderQuote) -> OrderQuoteOut:
    return OrderQuoteOut(
        purpose=q.purpose,
        taxable_minor=q.taxable_minor,
        cgst_minor=q.tax.cgst_minor,
        sgst_minor=q.tax.sgst_minor,
        igst_minor=q.tax.igst_minor,
        tax_minor=q.tax.tax_minor,
        gross_minor=q.gross_minor,
        currency=q.currency,
        description=q.description,
        tax_kind=q.tax.kind,
        place_of_supply=q.tax.place_of_supply,
        # P2 invoices: a seller without a GST number issues a bill of supply
        document_type="bill_of_supply" if q.tax.kind == "no_gst" else "invoice",
        # /P2 invoices
    )


@router.get("/payments/config", response_model=PaymentConfigOut)
async def payments_config(
    request: Request, session: AsyncSession = Depends(get_session)
) -> PaymentConfigOut:
    """What the pay page needs: whether online payment is on, the key id for the payment window
    (public, never a secret), the limits and whether your tax details are complete."""
    tenant_id = owner_workspace(request)
    engine = (await read_engine_settings_row(session))[0]
    provider = await factory.provider_for(request.app, session)
    resolved = await payment_settings.load(session, request.app.state.settings)
    return PaymentConfigOut(
        enabled=provider is not None,
        mode=provider.mode if provider is not None else None,
        key_id=(resolved.key_id or None) if provider is not None else None,
        min_top_up_minor=engine.payments.min_top_up_minor,
        max_payment_minor=engine.payments.max_payment_minor,
        currency="INR",
        tax_details_complete=profiles.is_complete(await profiles.get_profile(session, tenant_id)),
        prices_include_gst=engine.invoicing.prices_include_gst,
    )


@router.post("/payments/quote", response_model=OrderQuoteOut)
async def payments_quote(
    body: OrderQuoteRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> OrderQuoteOut:
    """What a payment would cost, GST included, without starting one."""
    tenant_id = owner_workspace(request)
    await _provider(request, session)
    await _owner_may_buy(session, tenant_id, body.purpose, body.add_on_id)  # P5 plans
    engine = (await read_engine_settings_row(session))[0]
    q = await orders.quote(
        session,
        tenant_id,
        engine,
        purpose=body.purpose,
        amount_minor=body.amount_minor,
        add_on_id=body.add_on_id,
    )
    return _quote_out(q)


@router.post("/payments/orders", response_model=OrderOut, status_code=201)
async def payments_create_order(
    body: CreateOrderRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> OrderOut:
    """Start a payment: returns the quote and what the payment window needs. The same
    `idempotency_key` gives back the same order (409 `idempotency_conflict` for a different
    amount). The money reaches the balance when the payment is confirmed, not here."""
    tenant_id = owner_workspace(request)
    provider = await _provider(request, session)
    await _owner_may_buy(session, tenant_id, body.purpose, body.add_on_id)  # P5 plans
    engine = (await read_engine_settings_row(session))[0]
    _, _, actor_label = actor_from_request(request)
    order, q, _created = await orders.start_order(
        session,
        provider,
        tenant_id,
        engine,
        purpose=body.purpose,
        amount_minor=body.amount_minor,
        add_on_id=body.add_on_id,
        idempotency_key=body.idempotency_key,
        created_by=actor_label,
    )
    await session.commit()
    user = getattr(request.state, "user", None)
    prefill = {"email": str(user.email), "name": str(user.name)} if user is not None else {}
    branding = engine.branding
    checkout = provider.checkout_params(
        ProviderOrder(
            order.provider_order_id or "", order.gross_minor, order.currency, str(order.id)
        ),
        name=branding.product_name,
        description=order.description,
        prefill=prefill,
        notes=orders.checkout_notes(order),
    )
    checkout["theme"] = {"color": branding.primary_color}
    return OrderOut(
        order_id=order.id,
        status=order.status,
        mode=provider.mode,
        quote=_quote_out(q),
        checkout=checkout,
    )


@router.get("/payments", response_model=PaymentViewList)
async def list_my_payments(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> PaymentViewList:
    """Payments this workspace has made, newest first. No fees and no payment-service detail:
    quote the reference if you ask us about one. Owners only."""
    tenant_id = owner_workspace(request)
    shown = ("captured", "partially_refunded", "refunded")
    total = (
        await session.execute(
            select(func.count())
            .select_from(Payment)
            .where(Payment.tenant_id == tenant_id, Payment.status.in_(shown))
        )
    ).scalar_one()
    rows = (
        await session.execute(
            select(Payment, PaymentOrder)
            .join(PaymentOrder, PaymentOrder.id == Payment.order_id)
            .where(Payment.tenant_id == tenant_id, Payment.status.in_(shown))
            .order_by(Payment.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return PaymentViewList(
        items=[
            PaymentView(
                id=p.id,
                date=p.captured_at or p.created_at,
                description=o.description,
                status=p.status,
                currency=p.currency,
                gross_minor=p.gross_minor,
                taxable_minor=p.taxable_minor,
                tax_minor=p.cgst_minor + p.sgst_minor + p.igst_minor,
                refunded_minor=p.refunded_minor,
                method=p.method,
                reference=p.provider_payment_id[-6:],
            )
            for p, o in rows
        ],
        total=int(total),
    )


@router.post("/payments/orders/{order_id}/verify", response_model=VerifyPaymentOut)
async def payments_verify(
    order_id: uuid.UUID,
    body: VerifyPaymentRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> VerifyPaymentOut:
    """The payment window says it succeeded: check its signature, ask the payment service what
    really happened, and add the money to the balance when it did. The browser is never trusted;
    the payment service's confirmation is what counts (the webhook does the same, and whichever
    comes first wins)."""
    tenant_id = owner_workspace(request)
    provider = await _provider(request, session)
    order = await session.get(PaymentOrder, order_id)
    if order is None or order.tenant_id != tenant_id:
        raise APIError(404, "That payment does not exist.", "order_not_found")
    if not order.provider_order_id or not provider.verify_checkout(
        provider_order_id=order.provider_order_id,
        provider_payment_id=body.provider_payment_id,
        signature=body.signature,
    ):
        raise APIError(
            400, "We could not confirm this payment's signature.", "payments_signature_invalid"
        )
    try:
        info = await provider.fetch_payment(body.provider_payment_id)
    except ProviderError:
        return VerifyPaymentOut(status="pending")  # the webhook or the sweep will finish it
    if info.status != "captured":
        return VerifyPaymentOut(status="pending")
    actor = actor_from_request(request)
    result = await apply_captured(session, info, source="checkout", actor=actor)
    await session.commit()
    await run_after_commit(request.app.state.session_factory, request.app.state.settings, result)
    if result.status not in ("credited", "duplicate"):
        return VerifyPaymentOut(status="pending")
    account = await accounts.ensure_account(session, tenant_id)
    return VerifyPaymentOut(
        status="captured", available_minor=await ledger.available_minor(session, account)
    )


# /P1 payments
