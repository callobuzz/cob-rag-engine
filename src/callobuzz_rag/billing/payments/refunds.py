"""Refunds from unused balance (P1): the balance drops first, then the provider pays the customer.

Operator only, and only from what the workspace has not spent: `refundable_minor` is the smallest
of the unused paid balance (promotional credit is never paid out, the BIL3 rule), what is still
unrefunded of this payment's taxable value, and the paid cash. The refund goes back to the
original payment (there is no transfer between workspaces).

Order matters (D6): the `REFUND -taxable` entry, keyed by OUR refund row, is written and committed
*before* the provider is called, so the customer cannot spend money that is on its way back. If the
provider says no, an `ADJUSTMENT +taxable` keyed `refund-failed:<refund id>` puts it back. A
`refund.processed` event (or the sweep) marks the refund processed and lets P2 issue the credit
note.

Lock order, everywhere: the billing account row first, then the refund, then the payment.
"""

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import Actor, record_audit
from callobuzz_rag.accounts.settings_store import EngineSettings
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing import accounts, ledger, lots
from callobuzz_rag.billing.invoicing import gst
from callobuzz_rag.billing.money import Money
from callobuzz_rag.billing.payments import documents
from callobuzz_rag.billing.payments.base import PaymentProvider, ProviderError
from callobuzz_rag.billing.payments.capture import AfterCommit, Processed
from callobuzz_rag.billing.pricing import TenantNotFoundError
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.payment import Payment, PaymentRefund
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

# Razorpay accepts a refund up to 180 days after the payment (its refund documentation); one
# constant, so a change is one edit.
REFUND_WINDOW_DAYS: Final = 180
LIVE_STATUSES: Final = ("requested", "pending", "processed")
PENDING_SETTLE_AFTER = timedelta(minutes=10)


def refund_deadline(payment: Payment) -> date | None:
    """The last day a refund of this payment is accepted, or None for a payment never captured."""
    if payment.captured_at is None:
        return None
    return (payment.captured_at + timedelta(days=REFUND_WINDOW_DAYS)).date()


async def _lock_account(session: AsyncSession, tenant_id: uuid.UUID) -> BillingAccount | None:
    """Lock the workspace's billing account; None when the workspace is gone."""
    try:
        return await accounts.account_for_update(session, tenant_id)
    except TenantNotFoundError:
        return None


async def refunded_taxable(session: AsyncSession, payment_id: uuid.UUID) -> int:
    """Taxable value already taken back from this payment (a failed refund took back nothing)."""
    return int(
        (
            await session.execute(
                select(func.coalesce(func.sum(PaymentRefund.taxable_minor), 0)).where(
                    PaymentRefund.payment_id == payment_id,
                    PaymentRefund.status.in_(LIVE_STATUSES),
                )
            )
        ).scalar_one()
    )


async def refundable_minor(session: AsyncSession, account: BillingAccount, payment: Payment) -> int:
    """What can still be refunded from `payment` out of the workspace's unused balance."""
    if payment.status not in ("captured", "partially_refunded"):
        return 0
    unused_paid = await ledger.available_minor(session, account) - await lots.live_credit(
        session, account.id
    )
    left = payment.taxable_minor - await refunded_taxable(session, payment.id)
    return max(min(unused_paid, await ledger.paid_cash_minor(session, account.id), left), 0)


def _refund_split(
    payment: Payment, taxable_minor: int, already: gst.TaxSplit | None
) -> gst.TaxSplit:
    """The tax a refund of `taxable_minor` takes back; the last refund takes exactly what is left
    so the parts add up to the payment."""
    original = gst.TaxSplit(
        kind="intra" if payment.cgst_minor else ("inter" if payment.igst_minor else "no_gst"),
        taxable_minor=payment.taxable_minor,
        cgst_minor=payment.cgst_minor,
        sgst_minor=payment.sgst_minor,
        igst_minor=payment.igst_minor,
        total_minor=payment.gross_minor,
        place_of_supply="",
    )
    if already is not None and taxable_minor == original.taxable_minor - already.taxable_minor:
        return gst.TaxSplit(
            original.kind,
            taxable_minor,
            original.cgst_minor - already.cgst_minor,
            original.sgst_minor - already.sgst_minor,
            original.igst_minor - already.igst_minor,
            original.total_minor - already.total_minor,
            original.place_of_supply,
        )
    return gst.refund_split(original, taxable_minor)


async def _already_refunded_split(session: AsyncSession, payment: Payment) -> gst.TaxSplit | None:
    rows = list(
        (
            await session.execute(
                select(PaymentRefund).where(
                    PaymentRefund.payment_id == payment.id,
                    PaymentRefund.status.in_(LIVE_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    return gst.TaxSplit(
        "inter",
        sum(r.taxable_minor for r in rows),
        sum(r.cgst_minor for r in rows),
        sum(r.sgst_minor for r in rows),
        sum(r.igst_minor for r in rows),
        sum(r.gross_minor for r in rows),
        "",
    )


def _not_refundable(refundable: int) -> APIError:
    return APIError(
        409,
        "That is more than can be refunded from this payment: the balance left from it is "
        f"{Money(refundable, 'INR').format()}.",
        "refund_exceeds_refundable",
        refundable_minor=refundable,
    )


async def request_refund(
    session: AsyncSession,
    settings: EngineSettings,
    payment_id: uuid.UUID,
    *,
    amount_minor: int,
    memo: str,
    confirm_amount_minor: int | None,
    idempotency_key: str,
    actor: Actor,
    request: Any = None,
    now: datetime | None = None,
) -> tuple[PaymentRefund, bool]:
    """Take `amount_minor` off the workspace's balance and record the refund (status `requested`).
    Returns `(refund, created)`; the same `idempotency_key` gives the same refund. The caller
    commits, then calls `send_refund`.

    Raises:
        APIError: 404 `payment_not_found`; 409 `refund_window_closed`, `refund_exceeds_refundable`,
            `idempotency_conflict`; 422 `confirm_mismatch`.
    """
    moment = now or datetime.now(UTC)
    probe = await session.get(Payment, payment_id)
    if probe is None:
        raise APIError(404, "That payment does not exist.", "payment_not_found")
    account = await _lock_account(session, probe.tenant_id)
    payment = (
        await session.execute(
            select(Payment)
            .where(Payment.id == payment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    existing = (
        await session.execute(
            select(PaymentRefund).where(PaymentRefund.idempotency_key == idempotency_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.payment_id != payment.id or existing.taxable_minor != amount_minor:
            raise ledger.IdempotencyConflict(idempotency_key)
        return existing, False
    if account is None:
        raise APIError(
            409,
            "This workspace no longer exists, so its balance cannot be refunded.",
            "refund_exceeds_refundable",
            refundable_minor=0,
        )
    deadline = refund_deadline(payment)
    if deadline is not None and moment.date() > deadline:
        raise APIError(
            409,
            "This payment is too old to refund through the payment service "
            f"(the last day was {deadline:%d %b %Y}). Return the money another way and record "
            "it as an adjustment.",
            "refund_window_closed",
        )
    limit = settings.billing.confirm_above_minor.get("INR")
    if limit is not None and amount_minor >= limit and confirm_amount_minor != amount_minor:
        raise APIError(
            422,
            "This is a large amount. Type the amount again to confirm it.",
            "confirm_mismatch",
            limit_minor=limit,
            currency="INR",
        )
    refundable = await refundable_minor(session, account, payment)
    if amount_minor > refundable:
        raise _not_refundable(refundable)
    split = _refund_split(payment, amount_minor, await _already_refunded_split(session, payment))
    refund = PaymentRefund(
        id=uuid.uuid4(),
        payment_id=payment.id,
        tenant_id=payment.tenant_id,
        account_id=payment.account_id,
        taxable_minor=amount_minor,
        cgst_minor=split.cgst_minor,
        sgst_minor=split.sgst_minor,
        igst_minor=split.igst_minor,
        gross_minor=split.total_minor,
        status="requested",
        memo=memo.strip(),
        requested_by=actor[2][:320],
        idempotency_key=idempotency_key,
        created_at=moment,
    )
    session.add(refund)
    await session.flush()
    [entry] = await ledger.append_entries(
        session,
        account.id,
        [
            ledger.NewEntry(
                ledger.EntryType.REFUND,
                -amount_minor,
                f"refund:{refund.id}",
                reference_type="payment_refund",
                reference_id=str(refund.id),
                memo=f"Refund of {payment.provider_payment_id}: {memo.strip()}"[:500],
                memo_public="Refund to your payment method",
            )
        ],
        actor=actor,
        now=moment,
    )
    refund.entry_id = entry.entry_id
    tenant = await session.get(Tenant, payment.tenant_id)
    await record_audit(
        session,
        request,
        "billing.refund_requested",
        tenant_id=payment.tenant_id,
        tenant_label=tenant.name if tenant else None,
        target_type="payment_refund",
        target_id=str(refund.id),
        details={
            "refund_id": str(refund.id),
            "payment_id": str(payment.id),
            "taxable_minor": amount_minor,
            "gross_minor": split.total_minor,
        },
        actor=actor,
    )
    await session.flush()
    return refund, True


async def send_refund(
    session_factory: Any,
    provider: PaymentProvider,
    refund_id: uuid.UUID,
    *,
    actor: Actor = ledger.SYSTEM_ACTOR,
    now: datetime | None = None,
) -> tuple[PaymentRefund, list[AfterCommit]]:
    """Ask the provider to pay a `requested` refund, in its own transaction (the balance entry
    was committed before this runs). A provider refusal puts the money back. Returns the refund as
    it is now and what to mail afterwards."""
    moment = now or datetime.now(UTC)
    async with session_factory() as session:
        probe = await session.get(PaymentRefund, refund_id)
        assert probe is not None
        await _lock_account(session, probe.tenant_id)
        refund = (
            await session.execute(
                select(PaymentRefund)
                .where(PaymentRefund.id == refund_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        payment = await session.get(Payment, refund.payment_id)
        assert payment is not None
        if refund.status != "requested":
            return refund, []
        after: list[AfterCommit] = []
        try:
            # a refund of ours the provider already holds (an answer we never saw) is adopted,
            # never sent again: the receipt is our refund id
            info = await provider.find_refund_by_receipt(
                payment.provider_payment_id, str(refund.id)
            )
            if info is None:
                info = await provider.refund(
                    payment.provider_payment_id,
                    amount_minor=refund.gross_minor,
                    receipt=str(refund.id),
                    notes={"cob_refund": str(refund.id)},
                )
        except ProviderError as exc:
            if not _definite_refusal(exc):
                # the provider may have made the refund: keep it `requested` (the balance stays
                # reduced) and let the sweep send it again, safely, by its receipt
                logger.warning(
                    "Refund %s not confirmed by the payment service (%s); the sweep retries it",
                    refund.id,
                    exc.kind,
                )
                return refund, []
            after.append(
                await _reverse(
                    session, refund, payment, f"the payment service said {exc.kind}", actor
                )
            )
            await session.commit()
            return refund, after
        refund.provider_refund_id = info.provider_refund_id
        refund.status = "pending"
        if info.status == "processed":
            after.append(await _processed(session, refund, payment, actor, moment))
        elif info.status == "failed":
            after.append(
                await _reverse(session, refund, payment, "the payment service refused it", actor)
            )
        await session.commit()
        return refund, after


def _definite_refusal(exc: ProviderError) -> bool:
    """True when the provider certainly did NOT make the refund: it said no (4xx), the
    credentials were refused, or the payment is unknown. A timeout, 5xx, 429 or an unreadable
    success answer is not definite: the refund may exist."""
    if exc.kind in ("auth", "not_found"):
        return True
    if exc.kind == "rejected":
        return exc.status is None or exc.status >= 400
    return False


async def _reverse(
    session: AsyncSession,
    refund: PaymentRefund,
    payment: Payment,
    reason: str,
    actor: Actor,
) -> AfterCommit:
    """The refund will not happen: put the money back (once: the key is ours)."""
    tenant = await session.get(Tenant, refund.tenant_id)
    name = tenant.name if tenant else "a deleted workspace"
    refund.status = "failed"
    refund.processed_at = datetime.now(UTC)
    if await session.get(BillingAccount, refund.account_id) is not None:
        [entry] = await ledger.append_entries(
            session,
            refund.account_id,
            [
                ledger.NewEntry(
                    ledger.EntryType.ADJUSTMENT,
                    refund.taxable_minor,
                    f"refund-failed:{refund.id}",
                    reference_type="payment_refund",
                    reference_id=str(refund.id),
                    memo=f"Refund {refund.id} could not be sent ({reason})"[:500],
                    memo_public="Refund could not be sent; amount returned to the balance",
                )
            ],
            actor=actor,
        )
        refund.reversal_entry_id = entry.entry_id
    await record_audit(
        session,
        None,
        "billing.refund_failed",
        tenant_id=refund.tenant_id,
        tenant_label=name,
        target_type="payment_refund",
        target_id=str(refund.id),
        details={
            "refund_id": str(refund.id),
            "payment_id": str(payment.id),
            "taxable_minor": refund.taxable_minor,
            "gross_minor": refund.gross_minor,
        },
        actor=actor,
    )
    return AfterCommit(
        kind="attention",
        tenant_id=refund.tenant_id,
        account_id=refund.account_id,
        tenant_name=name,
        provider_payment_id=payment.provider_payment_id,
        reason=(
            f"A refund of {Money(refund.gross_minor, 'INR').format()} could not be sent "
            f"({reason}). The amount was returned to the workspace's balance. Check the "
            "payment in the provider's dashboard before trying again: if it shows the refund, "
            "do not repeat it."
        ),
    )


async def _processed(
    session: AsyncSession,
    refund: PaymentRefund,
    payment: Payment,
    actor: Actor,
    now: datetime,
) -> AfterCommit:
    """The provider has paid the customer: mark it, count it on the payment, let P2 issue the
    credit note, tell the owner."""
    refund.status = "processed"
    refund.processed_at = now
    payment.refunded_minor += refund.gross_minor
    payment.status = (
        "refunded" if payment.refunded_minor >= payment.gross_minor else "partially_refunded"
    )
    await session.flush()
    await documents.on_refund_processed(session, refund)
    tenant = await session.get(Tenant, refund.tenant_id)
    name = tenant.name if tenant else "a deleted workspace"
    await record_audit(
        session,
        None,
        "billing.refund_issued",
        tenant_id=refund.tenant_id,
        tenant_label=name,
        target_type="payment_refund",
        target_id=str(refund.id),
        details={
            "refund_id": str(refund.id),
            "payment_id": str(payment.id),
            "taxable_minor": refund.taxable_minor,
            "gross_minor": refund.gross_minor,
        },
        actor=actor,
    )
    return AfterCommit(
        kind="refund",
        tenant_id=refund.tenant_id,
        account_id=refund.account_id,
        tenant_name=name,
        gross_minor=refund.gross_minor,
        taxable_minor=refund.taxable_minor,
        payment_id=payment.id,
        provider_payment_id=payment.provider_payment_id,
        # P2 invoices: tells the operator that the refund got no credit note
        reason=(
            "A refund went out, but no credit note could be issued: the deadline for the "
            "invoice's financial year has passed. The GST already paid stays paid."
            if refund.credit_note_skipped == "deadline"
            else ""
        ),
        # /P2 invoices
    )


async def settle_refund(
    session: AsyncSession,
    provider: PaymentProvider,
    refund_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> list[AfterCommit]:
    """Ask the provider where a refund stands and apply it: processed (credit note, mail) or failed
    (the money goes back). Replay-safe: a settled refund is left alone. Does not commit."""
    moment = now or datetime.now(UTC)
    probe = await session.get(PaymentRefund, refund_id)
    if probe is None:
        return []
    await _lock_account(session, probe.tenant_id)
    refund = (
        await session.execute(
            select(PaymentRefund)
            .where(PaymentRefund.id == refund_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if refund.status not in ("requested", "pending") or not refund.provider_refund_id:
        return []
    payment = await session.get(Payment, refund.payment_id)
    assert payment is not None
    info = await provider.fetch_refund(payment.provider_payment_id, refund.provider_refund_id)
    if info.status == "processed":
        return [await _processed(session, refund, payment, ledger.SYSTEM_ACTOR, moment)]
    if info.status == "failed":
        return [
            await _reverse(
                session,
                refund,
                payment,
                "the payment service could not pay it",
                ledger.SYSTEM_ACTOR,
            )
        ]
    return []


async def settle_event(
    session: AsyncSession,
    provider: PaymentProvider,
    name: str,
    ids: dict[str, str],
    *,
    now: datetime,
) -> Processed:
    """A `refund.processed` / `refund.failed` event. A refund we did not start (made in the
    provider's own dashboard) has nothing of ours to settle."""
    refund_pid = ids.get("refund")
    if not refund_pid:
        return Processed("ignored")
    refund = (
        await session.execute(
            select(PaymentRefund).where(PaymentRefund.provider_refund_id == refund_pid)
        )
    ).scalar_one_or_none()
    if refund is None:
        return Processed("ignored")
    after = await settle_refund(session, provider, refund.id, now=now)
    return Processed("done", attention=after)


async def settle_stale_pending(
    session_factory: Any, provider: PaymentProvider, *, now: datetime | None = None
) -> tuple[int, list[AfterCommit]]:
    """The sweep's pass over refunds still open after a while: `pending` ones (a webhook that came
    before we had stored the provider's id, or never came) are settled, and `requested` ones (the
    balance entry was committed but the send never finished) are sent again by their receipt."""
    moment = now or datetime.now(UTC)
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(PaymentRefund.id, PaymentRefund.status).where(
                    PaymentRefund.status.in_(("requested", "pending")),
                    PaymentRefund.created_at < moment - PENDING_SETTLE_AFTER,
                )
            )
        ).all()
    ids = [r.id for r in rows if r.status == "pending"]
    unsent = [r.id for r in rows if r.status == "requested"]
    settled = 0
    notes: list[AfterCommit] = []
    for refund_id in unsent:
        _, after = await send_refund(session_factory, provider, refund_id, now=moment)
        settled += len(after)
        notes.extend(after)
    for refund_id in ids:
        async with session_factory() as session:
            try:
                after = await settle_refund(session, provider, refund_id, now=moment)
            except ProviderError as exc:
                if exc.kind == "not_found":  # one unknown refund never stops the rest
                    continue
                raise
            await session.commit()
        settled += len(after)
        notes.extend(after)
    return settled, notes
