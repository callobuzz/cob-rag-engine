"""Money arrives only here: a captured payment becomes one ledger entry.

`apply_captured` is the **only** function that turns money received into a balance. The checkout
confirmation, the webhook worker, the nightly settlement check and the sweep all call it, and
whichever comes first wins; the rest find the payment and do nothing. In ONE transaction it

1. locks the order row,
2. returns at once when the payment is already recorded (the unique `provider_payment_id`),
3. refuses to credit anything that does not match the order (wrong amount, currency, workspace or
   an order already paid by another payment): the payment is stored as `mismatch` and the operator
   is told,
4. inserts the `payments` row and writes **one ledger entry keyed `rzp:<provider_payment_id>`**
   (the wallet gets the taxable value; GST is owed to the government), and for an add-on also the
   grant,
5. lets P2 issue the tax invoice (`documents.on_payment_captured`) and writes the audit row.

The caller commits, then calls `run_after_commit` (balance check, e-mail, webhook). A late capture
of an expired order is still credited: money received is never ignored.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import Actor, record_audit
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing import addons, ledger
from callobuzz_rag.billing.money import Money
from callobuzz_rag.billing.payments import documents
from callobuzz_rag.billing.payments.base import PaymentInfo
from callobuzz_rag.billing.payments.orders import OrderQuote
from callobuzz_rag.db.models.add_on import AddOn
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.payment import Payment, PaymentOrder
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

__all__ = [
    "AfterCommit",
    "CaptureResult",
    "OrderQuote",
    "Processed",
    "apply_captured",
    "attention_result",
    "run_after_commit",
]

Source = Literal["checkout", "webhook", "recon", "sweep"]
Status = Literal[
    "credited",
    "duplicate",
    "mismatch",
    "orphaned",
    "authorized",
    "not_captured",
    "unknown_order",
]


@dataclass(frozen=True, slots=True)
class AfterCommit:
    """What to do once the transaction has committed (mail and webhook never run inside it)."""

    kind: Literal["received", "attention", "refund"]
    tenant_id: uuid.UUID | None
    account_id: uuid.UUID | None
    tenant_name: str
    purpose_label: str = ""
    gross_minor: int = 0
    taxable_minor: int = 0
    currency: str = "INR"
    payment_id: uuid.UUID | None = None
    provider_payment_id: str = ""
    purpose: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CaptureResult:
    created: bool
    status: Status
    payment_id: uuid.UUID | None = None
    order_id: uuid.UUID | None = None
    after: tuple[AfterCommit, ...] = ()


Outcome = Literal["done", "ignored", "failed", "skipped"]


@dataclass(slots=True)
class Processed:
    """What handling one stored webhook event produced (`events.process`, `refunds.settle_event`)."""

    status: Outcome
    results: list[CaptureResult] = field(default_factory=list)
    attention: list[AfterCommit] = field(default_factory=list)


def attention_result(items: list[AfterCommit]) -> CaptureResult:
    """A follow-up that only tells the operator something (no money moved)."""
    return CaptureResult(False, "not_captured", after=tuple(items))


def _attention(
    order: PaymentOrder | None, tenant_name: str, info: PaymentInfo, reason: str
) -> AfterCommit:
    return AfterCommit(
        kind="attention",
        tenant_id=order.tenant_id if order is not None else None,
        account_id=order.account_id if order is not None else None,
        tenant_name=tenant_name,
        gross_minor=info.amount_minor,
        currency=info.currency,
        provider_payment_id=info.provider_payment_id,
        reason=reason,
    )


async def _find_order(session: AsyncSession, info: PaymentInfo) -> PaymentOrder | None:
    """The order a payment belongs to: by the provider's order id, else by the note we put on it."""
    if info.provider_order_id:
        found = (
            await session.execute(
                select(PaymentOrder).where(PaymentOrder.provider_order_id == info.provider_order_id)
            )
        ).scalar_one_or_none()
        if found is not None:
            return found
    note = info.notes.get("cob_order")
    if note:
        try:
            order_id = uuid.UUID(note)
        except ValueError:
            return None
        return await session.get(PaymentOrder, order_id)
    return None


def _mismatch(order: PaymentOrder, info: PaymentInfo) -> str | None:
    """Why this captured payment must not be credited to `order`, or None."""
    if info.amount_minor != order.gross_minor:
        return (
            f"The payment is {Money(info.amount_minor, 'INR').format()} but the order was "
            f"{Money(order.gross_minor, 'INR').format()}."
        )
    if info.currency != order.currency or info.currency != "INR":
        return f"The payment is in {info.currency}, not rupees."
    if order.provider_order_id and info.provider_order_id != order.provider_order_id:
        return "The payment belongs to a different provider order."
    account_note = info.notes.get("cob_account")
    if account_note and account_note != str(order.account_id):
        return "The payment note names a different account than the order."
    if order.status == "paid":
        return "The order was already paid by another payment (a double payment: refund one)."
    return None


def _label(order: PaymentOrder) -> str:
    if order.purpose == "plan":
        return f"Plan payment: {order.description}"[:200]
    if order.purpose == "add_on":
        return f"Payment: {order.description}"[:200]
    return "Top-up"


async def apply_captured(
    session: AsyncSession,
    info: PaymentInfo,
    *,
    source: Source,
    actor: Actor = ledger.SYSTEM_ACTOR,
    now: datetime | None = None,
    order_hint: PaymentOrder | None = None,
) -> CaptureResult:
    """Record a payment the provider says exists and, when it is captured and matches its order,
    credit it. Idempotent; see the module docstring. Does not commit.

    Raises:
        ledger.LedgerError, MoneyError: a bug or a corrupted account (nothing is half-written:
            the caller's transaction rolls back).
    """
    moment = now or datetime.now(UTC)
    # `order_hint` is the order a signed event named (a payment link's own id): it comes before
    # the provider-order and note lookups, which a link's payment may not satisfy
    found = order_hint or await _find_order(session, info)
    if found is None:
        logger.warning("Payment %s names no known order", info.provider_payment_id)
        return CaptureResult(
            False,
            "unknown_order",
            after=(
                _attention(
                    None,
                    "an unknown workspace",
                    info,
                    "A payment arrived that matches no order of ours. Check it in the "
                    "provider's dashboard.",
                ),
            ),
        )
    order = (
        await session.execute(
            select(PaymentOrder)
            .where(PaymentOrder.id == found.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    existing = (
        await session.execute(
            select(Payment).where(Payment.provider_payment_id == info.provider_payment_id)
        )
    ).scalar_one_or_none()
    if existing is not None and not (existing.status == "authorized" and info.status == "captured"):
        return CaptureResult(False, "duplicate", existing.id, order.id)

    tenant = await session.get(Tenant, order.tenant_id)
    tenant_name = tenant.name if tenant is not None else "a deleted workspace"

    if info.status != "captured":
        if info.status == "authorized" and existing is None:
            row = _new_payment(order, info, "authorized", source, moment)
            session.add(row)
            await session.flush()
            return CaptureResult(False, "authorized", row.id, order.id)
        return CaptureResult(False, "not_captured", existing.id if existing else None, order.id)

    reason = _mismatch(order, info)
    if reason is not None:
        row = existing or _new_payment(order, info, "mismatch", source, moment)
        row.status = "mismatch"
        row.gross_minor = info.amount_minor
        row.taxable_minor = row.cgst_minor = row.sgst_minor = row.igst_minor = 0
        if existing is None:
            session.add(row)
        await session.flush()
        await record_audit(
            session,
            None,
            "billing.payment_mismatch",
            tenant_id=order.tenant_id,
            tenant_label=tenant_name,
            target_type="payment",
            target_id=str(row.id),
            details={"payment_id": str(row.id), "reason": reason[:200]},
            actor=actor,
        )
        return CaptureResult(
            False,
            "mismatch",
            row.id,
            order.id,
            after=(_attention(order, tenant_name, info, reason),),
        )

    account = await session.get(BillingAccount, order.account_id)
    payment = existing or _new_payment(order, info, "captured", source, moment)
    payment.status = "captured"
    payment.captured_at = info.captured_at or moment
    if existing is None:
        session.add(payment)
    await session.flush()
    if account is None:
        reason = (
            "The workspace was deleted before this payment arrived. Refund it from the provider."
        )
        order.status = "paid"
        order.paid_at = moment
        await record_audit(
            session,
            None,
            "billing.payment_mismatch",
            tenant_id=order.tenant_id,
            tenant_label=tenant_name,
            target_type="payment",
            target_id=str(payment.id),
            details={"payment_id": str(payment.id), "reason": "workspace_deleted"},
            actor=actor,
        )
        return CaptureResult(
            False,
            "orphaned",
            payment.id,
            order.id,
            after=(_attention(order, tenant_name, info, reason),),
        )

    was_expired = order.status == "expired"
    key = f"rzp:{info.provider_payment_id}"
    credited_entry, fallback = await _credit(session, order, payment, info, key, actor, moment)
    payment.entry_id = credited_entry
    order.status = "paid"
    order.paid_at = moment
    await session.flush()
    await documents.on_payment_captured(session, payment, order, now=moment)
    await record_audit(
        session,
        None,
        "billing.payment_received",
        tenant_id=order.tenant_id,
        tenant_label=tenant_name,
        target_type="payment",
        target_id=str(payment.id),
        details={
            "payment_id": str(payment.id),
            "provider_payment_id": info.provider_payment_id,
            "purpose": order.purpose,
            "taxable_minor": order.taxable_minor,
            "gross_minor": order.gross_minor,
            "currency": order.currency,
            "entry_id": str(credited_entry),
            "source": source,
            **({"late": True} if was_expired else {}),
            **({"add_on_unavailable": fallback} if fallback else {}),
        },
        actor=actor,
    )
    after: list[AfterCommit] = [
        AfterCommit(
            kind="received",
            tenant_id=order.tenant_id,
            account_id=order.account_id,
            tenant_name=tenant_name,
            purpose_label=order.description,
            gross_minor=order.gross_minor,
            taxable_minor=order.taxable_minor,
            currency=order.currency,
            payment_id=payment.id,
            provider_payment_id=info.provider_payment_id,
            purpose=order.purpose,
        )
    ]
    if fallback:
        after.append(
            _attention(
                order,
                tenant_name,
                info,
                f"The add-on could not be applied ({fallback}); the money was added to the "
                "balance instead.",
            )
        )
    return CaptureResult(True, "credited", payment.id, order.id, after=tuple(after))


def _new_payment(
    order: PaymentOrder,
    info: PaymentInfo,
    status: str,
    source: str,
    moment: datetime,
) -> Payment:
    return Payment(
        id=uuid.uuid4(),
        order_id=order.id,
        tenant_id=order.tenant_id,
        account_id=order.account_id,
        provider=order.provider,
        provider_payment_id=info.provider_payment_id,
        method=(info.method or None) and info.method[:20],
        status=status,
        currency=order.currency,
        gross_minor=order.gross_minor,
        taxable_minor=order.taxable_minor,
        cgst_minor=order.cgst_minor,
        sgst_minor=order.sgst_minor,
        igst_minor=order.igst_minor,
        refunded_minor=0,
        fee_minor=info.fee_minor,
        fee_tax_minor=info.tax_minor,
        source=source,
        created_at=moment,
    )


async def _credit(
    session: AsyncSession,
    order: PaymentOrder,
    payment: Payment,
    info: PaymentInfo,
    key: str,
    actor: Actor,
    moment: datetime,
) -> tuple[uuid.UUID, str | None]:
    """Write the payment's one entry (and an add-on's grant). Returns `(entry id, fallback)`;
    `fallback` is why an add-on could not be applied and the money became balance instead."""
    top_up = ledger.NewEntry(
        ledger.EntryType.TOP_UP,
        order.taxable_minor,
        key,
        reference_type="payment",
        reference_id=str(payment.id),
        memo=f"Online payment {info.provider_payment_id} ({info.method or 'online'})"[:500],
        memo_public=_label(order),
    )
    add_on = await session.get(AddOn, order.add_on_id) if order.add_on_id else None
    if order.purpose != "add_on" or add_on is None:
        [result] = await ledger.append_entries(
            session, order.account_id, [top_up], actor=actor, now=moment
        )
        return result.entry_id, ("add-on no longer exists" if order.purpose == "add_on" else None)

    gateway = addons.GatewayPayment(info.provider_payment_id, key)
    if add_on.kind == "credit_pack":
        # the pack IS the payment's one entry (credit and bonus), never a top-up on top of it
        try:
            async with session.begin_nested():
                grant, _ = await addons.apply_paid_add_on(
                    session,
                    order.tenant_id,
                    add_on.id,
                    gateway=gateway,
                    price_minor=order.taxable_minor,
                    actor=actor,
                    now=moment,
                )
        except APIError as exc:
            [result] = await ledger.append_entries(
                session, order.account_id, [top_up], actor=actor, now=moment
            )
            return result.entry_id, exc.code
        assert grant.entry_id is not None
        return grant.entry_id, None
    # any other add-on: the payment tops the balance up, then the (funded) purchase takes its price
    [result] = await ledger.append_entries(
        session, order.account_id, [top_up], actor=actor, now=moment
    )
    try:
        async with session.begin_nested():
            await addons.apply_paid_add_on(
                session,
                order.tenant_id,
                add_on.id,
                gateway=gateway,
                price_minor=order.taxable_minor,
                actor=actor,
                now=moment,
            )
    except APIError as exc:
        return result.entry_id, exc.code
    return result.entry_id, None


# ---------------------------------------------------------------------------- after the commit


async def run_after_commit(
    session_factory: Any,
    settings: Any,
    result: CaptureResult,
    *,
    enqueue: Callable[..., None] | None = None,
    dispatch: Callable[..., Any] | None = None,
) -> None:
    """Balance transitions, e-mail and the webhook for a committed capture. Every step is best
    effort: a failure here never undoes or hides the money that was credited."""
    from callobuzz_rag.accounts.email.render import dashboard_link
    from callobuzz_rag.billing import hooks
    from callobuzz_rag.core.events import WebhookEvent

    if not result.after:
        return
    real_queue = enqueue is None  # P2 invoices: tests inject their own and never reach Celery
    if enqueue is None:
        from callobuzz_rag.accounts.email.dispatch import enqueue_email

        enqueue = enqueue_email
    if dispatch is None:
        from callobuzz_rag.core.webhooks import WebhookDispatcher

        dispatch = WebhookDispatcher(settings, session_factory).dispatch
    operator = (settings.superadmin_email or "").strip()
    for item in result.after:
        try:
            if real_queue and item.payment_id is not None and item.kind in ("received", "refund"):
                # P2 invoices: write the PDF now (the ten-minute sweep is the safety net)
                from callobuzz_rag.billing.invoicing.store import queue_renders

                try:
                    await queue_renders(session_factory, item.payment_id)
                except Exception as exc:
                    logger.warning("Tax document PDF not queued (%s)", type(exc).__name__)
            if item.kind == "refund":
                assert item.tenant_id is not None
                async with session_factory() as session:
                    from callobuzz_rag.accounts.lifecycle import owner_emails

                    refund_owners = await owner_emails(session, item.tenant_id)
                for to in refund_owners:
                    enqueue(
                        "refund_issued",
                        to,
                        {
                            "tenant_name": item.tenant_name,
                            "amount": Money(item.gross_minor, "INR").format(),
                            "billing_url": dashboard_link(settings.dashboard_url, "/plan"),
                        },
                        expires_s=24 * 3600,
                    )
                if item.reason and operator:  # P2 invoices: no credit note could be issued
                    enqueue(
                        "payment_attention",
                        operator,
                        {
                            "tenant_name": item.tenant_name,
                            "reason": item.reason,
                            "payments_url": dashboard_link(
                                settings.dashboard_url, "/admin/billing?tab=payments"
                            ),
                        },
                        expires_s=7 * 24 * 3600,
                    )
                continue
            if item.kind == "attention":
                if operator:
                    enqueue(
                        "payment_attention",
                        operator,
                        {
                            "tenant_name": item.tenant_name,
                            "reason": item.reason,
                            "payments_url": dashboard_link(
                                settings.dashboard_url, "/admin/billing?tab=payments"
                            ),
                        },
                        expires_s=7 * 24 * 3600,
                    )
                continue
            assert item.account_id is not None and item.tenant_id is not None
            await hooks.after_balance_change(session_factory, [item.account_id])
            async with session_factory() as session:
                from callobuzz_rag.accounts.lifecycle import owner_emails

                account = await session.get(BillingAccount, item.account_id)
                owners = await owner_emails(session, item.tenant_id)
                balance = account.balance_minor if account is not None else 0
            for to in owners:
                enqueue(
                    "payment_received",
                    to,
                    {
                        "tenant_name": item.tenant_name,
                        "amount": Money(item.gross_minor, "INR").format(),
                        "purpose_label": item.purpose_label,
                        "balance": Money(balance, "INR").format(),
                        "billing_url": dashboard_link(settings.dashboard_url, "/plan"),
                    },
                    expires_s=24 * 3600,
                )
            await dispatch(
                WebhookEvent.BILLING_PAYMENT_RECEIVED,
                {
                    "tenant_id": str(item.tenant_id),
                    "tenant_name": item.tenant_name,
                    "payment_id": str(item.payment_id),
                    "purpose": item.purpose,
                    "taxable_minor": item.taxable_minor,
                    "gross_minor": item.gross_minor,
                    "currency": item.currency,
                },
            )
        except Exception as exc:
            logger.error("Payment follow-up failed (%s)", type(exc).__name__)
