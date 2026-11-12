"""The operator's side of payments (P1): the seller's tax profile, payment settings, payment lists.

`/api/v1/admin/billing/...`. Admin only; every route answers 404 `billing_disabled` while billing
is off. Keys are stored encrypted and never returned.
"""

import logging
import time
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from datetime import time as dtime
from typing import Final, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.accounts.settings_store import (
    SellerTaxProfile,
    invalidate_engine_settings_cache,
    read_engine_settings_row,
    update_engine_settings,
)
from callobuzz_rag.api.deps import get_session, require_admin
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.payments import (
    CreditNoteInfo,  # P2 invoices
    PaymentDetail,
    PaymentEventListResponse,
    PaymentEventRow,
    PaymentLinkOut,
    PaymentLinkRequest,
    PaymentListResponse,
    PaymentRow,
    PaymentSettingsOut,
    PaymentSettingsRequest,
    PaymentStatusName,
    PaymentTestOut,
    RefundOut,
    RefundRequest,
    RetryOut,
    SellerTaxProfileOut,
)
from callobuzz_rag.billing.config import require_billing_enabled
from callobuzz_rag.billing.invoicing.gstin import state_of
from callobuzz_rag.billing.payments import events, factory, links, profiles, refunds
from callobuzz_rag.billing.payments import settings as payment_settings
from callobuzz_rag.billing.payments.base import ProviderError
from callobuzz_rag.billing.payments.capture import AfterCommit, attention_result, run_after_commit
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.payment import Payment, PaymentEvent, PaymentOrder, PaymentRefund
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

# P1 payments
router = APIRouter(
    prefix="/api/v1/admin/billing",
    tags=["payments"],
    dependencies=[Depends(require_admin), Depends(require_billing_enabled)],
)

_IST = timezone(timedelta(hours=5, minutes=30))
TEST_INTERVAL_S: Final = 10.0
_last_test: list[float] = [0.0]  # monotonic time of the last connection test in this process


# P2 invoices
async def credit_note_info(session: AsyncSession, payment: Payment) -> CreditNoteInfo:
    """Can a refund of this payment still get a credit note, and until when."""
    from callobuzz_rag.billing.invoicing import documents

    invoice = await documents.invoice_of_payment(session, payment.id)
    if invoice is None:
        return CreditNoteInfo(possible=False)
    deadline = documents.credit_note_deadline(documents.local_date(invoice.issued_at))
    return CreditNoteInfo(
        possible=documents.local_date(datetime.now(UTC)) <= deadline,
        deadline=deadline,
        invoice_number=invoice.number,
    )


# /P2 invoices


def _example_number(prefix: str, now: datetime | None = None) -> str:
    """What the next invoice number looks like: `PREFIX/YY-YY/00001` (Indian financial year)."""
    # P2 invoices
    from callobuzz_rag.billing.invoicing.numbering import financial_year, format_number

    return format_number(prefix, financial_year(now or datetime.now(UTC)), 1)
    # /P2 invoices


def _profile_out(profile: SellerTaxProfile) -> SellerTaxProfileOut:
    return SellerTaxProfileOut(
        **profile.model_dump(), number_example=_example_number(profile.invoice_prefix)
    )


@router.get("/tax-profile", response_model=SellerTaxProfileOut)
async def get_tax_profile(session: AsyncSession = Depends(get_session)) -> SellerTaxProfileOut:
    """Your GST details as they appear on a tax invoice, with an example invoice number."""
    return _profile_out((await read_engine_settings_row(session))[0].invoicing)


@router.put("/tax-profile", response_model=SellerTaxProfileOut)
async def put_tax_profile(
    body: SellerTaxProfile, request: Request, session: AsyncSession = Depends(get_session)
) -> SellerTaxProfileOut:
    """Replace your GST details. The GST number's state must be your company's state
    (422 `gstin_state_mismatch`); a wrong number is 422 `invalid_gstin`."""
    current = (await read_engine_settings_row(session))[0]
    if (
        body.gstin is not None
        and current.company.state_code is not None
        and state_of(body.gstin) != current.company.state_code
    ):
        raise APIError(
            422,
            "The GST number's state does not match your company's state. "
            "Check the company details.",
            "gstin_state_mismatch",
        )
    _, _, actor_label = actor_from_request(request)
    try:
        settings, changed = await update_engine_settings(
            session, {"invoicing": body.model_dump(mode="json")}, actor_label=actor_label
        )
    except ValidationError as exc:
        from fastapi.exceptions import RequestValidationError

        raise RequestValidationError(
            [{**e, "loc": ("body", *e["loc"])} for e in exc.errors(include_url=False)]
        ) from exc
    if changed:
        await record_audit(
            session,
            request,
            "billing.tax_profile_updated",
            target_type="settings",
            target_id="invoicing",
            details={"changed": sorted(name.split(".", 1)[1] for name in changed)},
        )
        await session.commit()
        invalidate_engine_settings_cache()
    return _profile_out(settings.invoicing)


# ------------------------------------------------------------------ payment settings


def _mode(key_id: str) -> str | None:
    if key_id.startswith("rzp_test_"):
        return "test"
    return "live" if key_id.startswith("rzp_live_") else None


async def _settings_out(
    session: AsyncSession, request: Request, resolved: payment_settings.ResolvedSettings
) -> PaymentSettingsOut:
    limits = (await read_engine_settings_row(session))[0].payments
    return PaymentSettingsOut(
        provider=resolved.provider,
        key_id=resolved.key_id or None,
        mode=_mode(resolved.key_id),
        key_secret_set=bool(resolved.key_secret),
        webhook_secret_set=bool(resolved.webhook_secret),
        source=resolved.source,
        enabled=factory.payment_provider(resolved) is not None,
        webhook_url=payment_settings.webhook_url(request.app.state.settings, str(request.base_url)),
        events=list(payment_settings.WEBHOOK_EVENTS),
        min_top_up_minor=limits.min_top_up_minor,
        max_payment_minor=limits.max_payment_minor,
        link_expiry_days=limits.link_expiry_days,
    )


@router.get("/payments/settings", response_model=PaymentSettingsOut)
async def get_payment_settings(
    request: Request, session: AsyncSession = Depends(get_session)
) -> PaymentSettingsOut:
    """The payment provider in force, whether its keys are saved (never the keys), the address
    to paste into Razorpay's webhook settings and the events to tick there."""
    resolved = await payment_settings.load(session, request.app.state.settings)
    return await _settings_out(session, request, resolved)


@router.put("/payments/settings", response_model=PaymentSettingsOut)
async def put_payment_settings(
    body: PaymentSettingsRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PaymentSettingsOut:
    """Save the provider, key id and the two secrets (stored encrypted; send a secret only to
    change it) and the payment limits. A key set in the engine's environment still wins."""
    engine_config = request.app.state.settings
    _, _, actor_label = actor_from_request(request)
    current = (await read_engine_settings_row(session))[0].payments
    new_min = (
        body.min_top_up_minor if body.min_top_up_minor is not None else current.min_top_up_minor
    )
    new_max = (
        body.max_payment_minor if body.max_payment_minor is not None else current.max_payment_minor
    )
    if new_min > new_max:
        raise APIError(
            422,
            "The smallest top-up cannot be larger than the largest payment.",
            "amount_out_of_range",
        )
    stored_before = await payment_settings.read_row(session)
    _, outcome = await payment_settings.save(
        session,
        engine_config,
        provider=body.provider,
        key_id=body.key_id,
        key_secret=body.key_secret.get_secret_value() if body.key_secret is not None else None,
        webhook_secret=(
            body.webhook_secret.get_secret_value() if body.webhook_secret is not None else None
        ),
        actor_label=actor_label,
    )
    changed: list[str] = []
    if (stored_before.provider if stored_before else "none") != body.provider:
        changed.append("provider")
    if body.key_id is not None and (body.key_id.strip() or None) != (
        stored_before.key_id if stored_before else None
    ):
        changed.append("key_id")
    limit_changes = {
        name: value
        for name, value in (
            ("min_top_up_minor", body.min_top_up_minor),
            ("max_payment_minor", body.max_payment_minor),
            ("link_expiry_days", body.link_expiry_days),
        )
        if value is not None
    }
    if limit_changes:
        try:
            _, limit_diff = await update_engine_settings(
                session, {"payments": limit_changes}, actor_label=actor_label
            )
        except ValidationError as exc:
            from fastapi.exceptions import RequestValidationError

            raise RequestValidationError(
                [{**e, "loc": ("body", *e["loc"])} for e in exc.errors(include_url=False)]
            ) from exc
        changed.extend(name.split(".", 1)[1] for name in limit_diff)
    if changed or any(v != "unchanged" for v in outcome.values()):
        await record_audit(
            session,
            request,
            "payments.settings_updated",
            target_type="settings",
            target_id="payments",
            details={"provider": body.provider, "changed": changed, **outcome},
        )
    await session.commit()
    invalidate_engine_settings_cache()
    resolved = await payment_settings.load(session, engine_config)
    return await _settings_out(session, request, resolved)


@router.post("/payments/settings/test", response_model=PaymentTestOut)
async def test_payment_settings(
    request: Request, session: AsyncSession = Depends(get_session)
) -> PaymentTestOut:
    """Check that the saved keys work (one harmless read from the provider). Once every ten
    seconds."""
    now = time.monotonic()
    wait = TEST_INTERVAL_S - (now - _last_test[0])
    if wait > 0:
        raise APIError(
            429,
            "You just tested. Please wait a few seconds and try again.",
            "payments_test_too_soon",
            headers={"Retry-After": str(int(wait) + 1)},
        )
    _last_test[0] = now
    provider = await factory.provider_for(request.app, session)
    if provider is None:
        raise APIError(
            422,
            "Payments are not set up yet: add your Razorpay key id and secret first.",
            "payments_test_failed",
            reason="not_configured",
        )
    try:
        await provider.test_connection()
    except ProviderError as exc:
        reason = "auth" if exc.kind == "auth" else "unreachable"
        raise APIError(
            422,
            "Razorpay did not accept these keys."
            if reason == "auth"
            else "We could not reach Razorpay. Please try again in a moment.",
            "payments_test_failed",
            reason=reason,
        ) from None
    return PaymentTestOut(ok=True, mode=provider.mode)


# ------------------------------------------------------------------ payments, events, refunds


def _row(payment: Payment, order: PaymentOrder, names: dict[uuid.UUID, str]) -> PaymentRow:
    return PaymentRow(
        id=payment.id,
        tenant_id=payment.tenant_id,
        tenant_name=names.get(payment.tenant_id, "Deleted workspace"),
        provider_payment_id=payment.provider_payment_id,
        purpose=order.purpose,
        description=order.description,
        method=payment.method,
        status=payment.status,
        currency=payment.currency,
        gross_minor=payment.gross_minor,
        taxable_minor=payment.taxable_minor,
        tax_minor=payment.cgst_minor + payment.sgst_minor + payment.igst_minor,
        refunded_minor=payment.refunded_minor,
        fee_minor=payment.fee_minor,
        fee_tax_minor=payment.fee_tax_minor,
        captured_at=payment.captured_at,
        settled_at=payment.settled_at,
        created_at=payment.created_at,
    )


async def _names(session: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    rows = (await session.execute(select(Tenant).where(Tenant.id.in_(ids)))).scalars().all()
    return {t.id: t.name for t in rows}


def _event_row(event: PaymentEvent) -> PaymentEventRow:
    return PaymentEventRow(
        id=event.id,
        event_id=event.event_id,
        event=event.event,
        status=event.status,
        attempts=event.attempts,
        error=event.error,
        received_at=event.received_at,
        processed_at=event.processed_at,
    )


def refund_out(refund: PaymentRefund) -> RefundOut:
    return RefundOut(
        id=refund.id,
        payment_id=refund.payment_id,
        status=refund.status,
        taxable_minor=refund.taxable_minor,
        cgst_minor=refund.cgst_minor,
        sgst_minor=refund.sgst_minor,
        igst_minor=refund.igst_minor,
        gross_minor=refund.gross_minor,
        provider_refund_id=refund.provider_refund_id,
        memo=refund.memo,
        requested_by=refund.requested_by,
        created_at=refund.created_at,
        processed_at=refund.processed_at,
        credit_note_skipped=refund.credit_note_skipped,  # P2 invoices
    )


@router.get("/payments", response_model=PaymentListResponse)
async def list_payments(
    tenant_id: uuid.UUID | None = Query(None, description="Only this workspace"),
    status: PaymentStatusName | None = Query(None, description="Only this status"),
    start: date | None = Query(None, description="First day (UTC), inclusive"),
    end: date | None = Query(None, description="Last day (UTC), inclusive"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> PaymentListResponse:
    """Payments received, newest first, with each payment's fee (the operator's side only)."""
    if start is not None and end is not None and start > end:
        raise APIError(422, "start must be on or before end", "invalid_range")
    where = []
    if tenant_id is not None:
        where.append(Payment.tenant_id == tenant_id)
    if status is not None:
        where.append(Payment.status == status)
    if start is not None:
        where.append(Payment.created_at >= datetime.combine(start, dtime.min, tzinfo=UTC))
    if end is not None:
        where.append(
            Payment.created_at < datetime.combine(end + timedelta(days=1), dtime.min, tzinfo=UTC)
        )
    total = (
        await session.execute(select(func.count()).select_from(Payment).where(*where))
    ).scalar_one()
    rows = (
        await session.execute(
            select(Payment, PaymentOrder)
            .join(PaymentOrder, PaymentOrder.id == Payment.order_id)
            .where(*where)
            .order_by(Payment.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    names = await _names(session, {p.tenant_id for p, _ in rows})
    return PaymentListResponse(items=[_row(p, o, names) for p, o in rows], total=int(total))


@router.get("/payments/events", response_model=PaymentEventListResponse)
async def list_payment_events(
    status: Literal["failed", "pending"] | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> PaymentEventListResponse:
    """Webhook deliveries that failed or are waiting (never the payload)."""
    wanted = (status,) if status else ("failed", "pending")
    rows = (
        (
            await session.execute(
                select(PaymentEvent)
                .where(PaymentEvent.status.in_(wanted))
                .order_by(PaymentEvent.received_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return PaymentEventListResponse(items=[_event_row(e) for e in rows])


@router.post("/payments/events/{event_id}/retry", response_model=RetryOut, status_code=202)
async def retry_payment_event(
    event_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> RetryOut:
    """Run a failed or waiting delivery again now. An event that was processed is not run again."""
    event = await session.get(PaymentEvent, event_id)
    if event is None:
        raise APIError(404, "That event does not exist.", "payment_event_not_found")
    if event.status not in ("failed", "pending"):
        raise APIError(
            409,
            "That event was already processed, so there is nothing to retry.",
            "payment_event_not_retryable",
        )
    event.status = "pending"
    event.error = None
    await session.commit()
    events.queue_event(event.event_id)
    return RetryOut(queued=True)


async def events_for(session: AsyncSession, provider_payment_id: str) -> list[PaymentEvent]:
    """The stored deliveries that name this payment (PostgreSQL JSONB containment)."""
    return list(
        (
            await session.execute(
                select(PaymentEvent)
                .where(PaymentEvent.entity_ids.contains({"payment": provider_payment_id}))
                .order_by(PaymentEvent.received_at)
                .limit(50)
            )
        )
        .scalars()
        .all()
    )


@router.get("/payments/{payment_id}", response_model=PaymentDetail)
async def get_payment(
    payment_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> PaymentDetail:
    """One payment: its order, the deliveries about it, its refunds and what can still be
    refunded."""
    payment = await session.get(Payment, payment_id)
    order = await session.get(PaymentOrder, payment.order_id) if payment is not None else None
    if payment is None or order is None:
        raise APIError(404, "That payment does not exist.", "payment_not_found")
    names = await _names(session, {payment.tenant_id})
    refund_rows = list(
        (
            await session.execute(
                select(PaymentRefund)
                .where(PaymentRefund.payment_id == payment.id)
                .order_by(PaymentRefund.created_at)
            )
        )
        .scalars()
        .all()
    )
    account = await session.get(BillingAccount, payment.account_id)
    refundable = (
        await refunds.refundable_minor(session, account, payment) if account is not None else 0
    )
    return PaymentDetail(
        **_row(payment, order, names).model_dump(),
        order_id=order.id,
        order_channel=order.channel,
        cgst_minor=payment.cgst_minor,
        sgst_minor=payment.sgst_minor,
        igst_minor=payment.igst_minor,
        tax_kind=order.tax_kind,
        place_of_supply=order.place_of_supply,
        provider_order_id=order.provider_order_id,
        settlement_id=payment.settlement_id,
        events=[_event_row(e) for e in await events_for(session, payment.provider_payment_id)],
        refunds=[refund_out(r) for r in refund_rows],
        refundable_minor=refundable,
        refund_deadline=refunds.refund_deadline(payment),
        credit_note=await credit_note_info(session, payment),  # P2 invoices
    )


async def _follow_up(request: Request, after: list[AfterCommit]) -> None:
    if after:
        await run_after_commit(
            request.app.state.session_factory,
            request.app.state.settings,
            attention_result(after),
        )


@router.post("/payments/{payment_id}/refunds", response_model=RefundOut, status_code=201)
async def refund_payment(
    payment_id: uuid.UUID,
    body: RefundRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> RefundOut:
    """Refund part of a payment from the workspace's unused balance, to the way it paid. The
    amount is taken off the balance first; if the payment service refuses, it is put back
    (502 `refund_failed`). Above the confirmation limit the amount must be typed again
    (`confirm_amount_minor`). The same `idempotency_key` gives the same refund."""
    provider = await factory.provider_for(request.app, session)
    if provider is None:
        raise APIError(404, "Online payments are not switched on here.", "payments_disabled")
    engine = (await read_engine_settings_row(session))[0]
    actor = actor_from_request(request)
    refund, created = await refunds.request_refund(
        session,
        engine,
        payment_id,
        amount_minor=body.amount_minor,
        memo=body.memo,
        confirm_amount_minor=body.confirm_amount_minor,
        idempotency_key=body.idempotency_key,
        actor=actor,
        request=request,
    )
    await session.commit()
    if created:
        await _hook(request, refund.account_id)
    else:
        response.status_code = 200
    after: list[AfterCommit] = []
    if refund.status == "requested":
        refund, after = await refunds.send_refund(
            request.app.state.session_factory, provider, refund.id, actor=actor
        )
        await _hook(request, refund.account_id)
    await _follow_up(request, after)
    if refund.status == "requested" and created:
        # the payment service did not answer: the refund is kept and sent again automatically
        response.status_code = 202
    if refund.status == "failed" and created:
        raise APIError(
            502,
            "The payment service could not send the refund. The amount was put back on the "
            "balance. Check the payment in Razorpay before trying again.",
            "refund_failed",
            refund_id=str(refund.id),
        )
    return refund_out(refund)


async def _hook(request: Request, account_id: uuid.UUID) -> None:
    from callobuzz_rag.billing import hooks

    try:
        await hooks.after_balance_change(request.app.state.session_factory, [account_id])
    except Exception:
        logger.warning("Balance hook failed after a refund", exc_info=True)


@router.post("/accounts/{tenant_id}/payment-links", response_model=PaymentLinkOut, status_code=201)
async def create_payment_link(
    tenant_id: uuid.UUID,
    body: PaymentLinkRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PaymentLinkOut:
    """Make a link a customer can open to pay (UPI, card or net banking), and optionally e-mail
    it to the workspace's owners. Copy `short_url` to send it by WhatsApp yourself. It works for
    `payments.link_expiry_days`."""
    from callobuzz_rag.accounts.email.dispatch import enqueue_email
    from callobuzz_rag.accounts.email.render import dashboard_link
    from callobuzz_rag.accounts.lifecycle import owner_emails
    from callobuzz_rag.billing.money import Money

    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise APIError(404, "That workspace does not exist.", "tenant_not_found")
    provider = await factory.provider_for(request.app, session)
    if provider is None:
        raise APIError(404, "Online payments are not switched on here.", "payments_disabled")
    engine = (await read_engine_settings_row(session))[0]
    profile = await profiles.get_profile(session, tenant_id)
    owners = await owner_emails(session, tenant_id)
    _, _, actor_label = actor_from_request(request)
    order, quote = await links.create_link(
        session,
        provider,
        tenant_id,
        engine,
        purpose=body.purpose,
        amount_minor=body.amount_minor,
        add_on_id=body.add_on_id,
        customer_name=(profile.legal_name if profile else tenant.name),
        customer_email=(profile.email_for_invoices if profile else None)
        or (owners[0] if owners else None),
        created_by=actor_label,
    )
    await record_audit(
        session,
        request,
        "billing.payment_link_created",
        tenant_id=tenant_id,
        tenant_label=tenant.name,
        target_type="payment_order",
        target_id=str(order.id),
        details={
            "order_id": str(order.id),
            "purpose": order.purpose,
            "gross_minor": order.gross_minor,
        },
    )
    await session.commit()
    emailed = False
    if body.send_email and owners:
        context = {
            **links.link_context(order, tenant.name, Money(order.gross_minor, "INR").format()),
            "pay_url": order.short_url
            or dashboard_link(request.app.state.settings.dashboard_url, "/plan"),
        }
        for to in owners:
            try:
                enqueue_email("payment_link", to, context, expires_s=7 * 24 * 3600)
                emailed = True
            except Exception as exc:
                logger.error("Payment link mail not queued (%s)", type(exc).__name__)
    assert order.short_url is not None and order.expires_at is not None
    return PaymentLinkOut(
        order_id=order.id,
        short_url=order.short_url,
        expires_at=order.expires_at,
        gross_minor=quote.gross_minor,
        emailed=emailed,
    )


# /P1 payments
