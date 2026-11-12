"""The webhook's events: stored at once, processed by a worker, always against the provider's truth.

The route only verifies the signature, stores the event by its id (payload encrypted: it holds a
customer's e-mail and phone) and returns 200. A worker then processes it: the payload only *names*
entities, and the amount, status and order are fetched from the provider, so a forged or stale
payload cannot move money and events can arrive in any order (a refund before its capture, a
failure after a success). A failed attempt is retried with a back-off (1 min, 5 min, 30 min, 2 h,
12 h); the sixth failure tells the operator. The sweep (`sweep`) also re-reads the orders still
waiting, so a webhook that was never delivered (or switched off after a day of errors) loses
nothing.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.billing import ledger
from callobuzz_rag.billing.payments import capture, refunds
from callobuzz_rag.billing.payments.base import PaymentProvider, ProviderError, WebhookEvent
from callobuzz_rag.billing.payments.capture import AfterCommit, CaptureResult, Processed
from callobuzz_rag.billing.payments.orders import ORDER_LIFETIME
from callobuzz_rag.core.secrets import FernetEncryptor
from callobuzz_rag.db.models.payment import Payment, PaymentEvent, PaymentOrder
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

BACKOFF: tuple[timedelta, ...] = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=12),
)
MAX_ATTEMPTS = len(BACKOFF) + 1
PENDING_AFTER = timedelta(seconds=60)  # a stored event nobody picked up for this long
AUTHORIZED_WATCH = timedelta(days=3)
SWEEP_BATCH = 100

CAPTURE_EVENTS = frozenset({"payment.captured", "order.paid", "payment_link.paid"})
REFUND_EVENTS = frozenset({"refund.processed", "refund.failed"})


async def store_event(
    session: AsyncSession,
    encryptor: FernetEncryptor,
    event: WebhookEvent,
    raw_body: bytes,
    *,
    provider: str = "razorpay",
    now: datetime | None = None,
) -> PaymentEvent | None:
    """Keep a delivery under its event id. Returns the new row, or None when this id was already
    stored (the provider retries; a duplicate is a no-op). The caller commits."""
    existing = (
        await session.execute(select(PaymentEvent).where(PaymentEvent.event_id == event.event_id))
    ).scalar_one_or_none()
    if existing is not None:
        return None
    row = PaymentEvent(
        id=uuid.uuid4(),
        provider=provider,
        event_id=event.event_id,
        event=event.event,
        entity_ids=dict(event.entity_ids),
        payload_encrypted=encryptor.encrypt(raw_body.decode("utf-8", errors="replace")),
        status="pending",
        attempts=0,
        received_at=now or datetime.now(UTC),
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:  # a concurrent delivery of the same event won
        return None
    return row


def due_at(event: PaymentEvent) -> datetime:
    """When a failed event may be tried again."""
    index = min(max(event.attempts, 1), len(BACKOFF)) - 1
    return (event.processed_at or event.received_at) + BACKOFF[index]


async def process(
    session: AsyncSession,
    provider: PaymentProvider,
    event_id: str,
    *,
    now: datetime | None = None,
) -> Processed:
    """Process one stored event and commit. Idempotent: a done or ignored event is skipped, and
    a replayed payment is a no-op in `apply_captured`."""
    moment = now or datetime.now(UTC)
    event = (
        await session.execute(
            select(PaymentEvent)
            .where(PaymentEvent.event_id == event_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if event is None or event.status not in ("pending", "failed"):
        return Processed("skipped")
    event.status = "processing"
    event.attempts += 1
    await session.flush()
    event_pk = event.id
    out = Processed("done")
    failure: str | None = None
    try:
        async with session.begin_nested():
            out = await _handle(session, provider, event, moment)
    except ProviderError as exc:
        failure = f"ProviderError:{exc.kind}{':' + exc.code if exc.code else ''}"[:200]
    except Exception as exc:
        logger.exception("Payment event %s could not be processed", event_id)
        failure = type(exc).__name__[:200]
    # a rolled-back savepoint expires what it touched: read the row again before writing to it
    row = await session.get(PaymentEvent, event_pk, populate_existing=True)
    assert row is not None
    if failure is not None:
        row.status = "failed"
        row.error = failure
        out = Processed("failed")
    else:
        row.status = out.status if out.status in ("done", "ignored") else "done"
        row.error = None
    row.processed_at = moment
    if row.status == "failed" and row.attempts >= MAX_ATTEMPTS:
        out.attention.append(
            AfterCommit(
                kind="attention",
                tenant_id=None,
                account_id=None,
                tenant_name="Payments",
                reason=(
                    f"A payment event ({row.event}) failed {row.attempts} times "
                    f"({row.error}). Open the payments page and retry it, or check the "
                    "provider's dashboard."
                ),
            )
        )
    await session.commit()
    return out


async def _handle(
    session: AsyncSession, provider: PaymentProvider, event: PaymentEvent, now: datetime
) -> Processed:
    name = event.event
    ids: dict[str, str] = dict(event.entity_ids or {})
    if name in CAPTURE_EVENTS:
        return await _captured(session, provider, ids)
    if name == "payment.failed":
        return await _failed(session, provider, ids)
    if name in REFUND_EVENTS:
        return await refunds.settle_event(session, provider, name, ids, now=now)
    if name.startswith("payment.dispute."):
        return await _dispute(session, name, ids)
    return Processed("ignored")


async def _captured(
    session: AsyncSession, provider: PaymentProvider, ids: dict[str, str]
) -> Processed:
    if ids.get("payment"):
        infos = [await provider.fetch_payment(ids["payment"])]
    elif ids.get("order"):
        infos = await provider.payments_of_order(ids["order"])
    else:
        return Processed("ignored")
    hint: PaymentOrder | None = None
    if ids.get("payment_link"):  # the signed link id names the order, whatever the notes say
        hint = (
            await session.execute(
                select(PaymentOrder).where(PaymentOrder.link_id == ids["payment_link"])
            )
        ).scalar_one_or_none()
    results = [
        await capture.apply_captured(session, info, source="webhook", order_hint=hint)
        for info in infos
        if info.status in ("captured", "authorized")
    ]
    return Processed("done", results=results)


async def _failed(
    session: AsyncSession, provider: PaymentProvider, ids: dict[str, str]
) -> Processed:
    if not ids.get("payment"):
        return Processed("ignored")
    info = await provider.fetch_payment(ids["payment"])
    if info.status != "failed":
        return Processed("ignored")  # it has moved on (captured since): that event handles it
    order = await capture._find_order(session, info)
    if order is None:
        return Processed("ignored")
    known = (
        await session.execute(
            select(Payment).where(Payment.provider_payment_id == info.provider_payment_id)
        )
    ).scalar_one_or_none()
    if known is None:
        row = capture._new_payment(order, info, "failed", "webhook", datetime.now(UTC))
        row.taxable_minor = row.cgst_minor = row.sgst_minor = row.igst_minor = 0
        session.add(row)
    live = (
        await session.execute(
            select(Payment.id).where(
                Payment.order_id == order.id, Payment.status.in_(("captured", "authorized"))
            )
        )
    ).first()
    if order.status == "created" and live is None:
        order.status = "failed"
    await session.flush()
    return Processed("done")


async def _dispute(session: AsyncSession, name: str, ids: dict[str, str]) -> Processed:
    """A customer's bank disputed a payment. Created: tell the operator. Lost: take the money
    back out of the balance with an adjustment. Won and closed: nothing changes."""
    pay_id = ids.get("payment")
    payment = (
        (
            await session.execute(select(Payment).where(Payment.provider_payment_id == pay_id))
        ).scalar_one_or_none()
        if pay_id
        else None
    )
    tenant = await session.get(Tenant, payment.tenant_id) if payment is not None else None
    tenant_name = tenant.name if tenant is not None else "a workspace"
    attention = AfterCommit(
        kind="attention",
        tenant_id=payment.tenant_id if payment else None,
        account_id=payment.account_id if payment else None,
        tenant_name=tenant_name,
        provider_payment_id=pay_id or "",
    )
    if name == "payment.dispute.created":
        return Processed(
            "done",
            attention=[
                _with_reason(
                    attention,
                    "A customer's bank opened a dispute on a payment. Reply in the provider's "
                    "dashboard before the deadline.",
                )
            ],
        )
    if name != "payment.dispute.lost" or payment is None:
        return Processed("done")
    dispute_id = ids.get("dispute") or f"{pay_id}"
    # the key is never `rzp:<id>`: that is the payment's own capture entry
    gross = payment.gross_minor or 1
    keep = max(gross - payment.refunded_minor, 0)
    taxable_left = payment.taxable_minor * keep // gross
    if taxable_left > 0:
        try:
            async with session.begin_nested():
                await ledger.append_entries(
                    session,
                    payment.account_id,
                    [
                        ledger.NewEntry(
                            ledger.EntryType.ADJUSTMENT,
                            -taxable_left,
                            f"rzp-dispute:{dispute_id}",
                            reference_type="payment",
                            reference_id=str(payment.id),
                            memo=f"Chargeback lost on {pay_id}",
                            memo_public="Payment reversed by the bank",
                        )
                    ],
                    actor=ledger.SYSTEM_ACTOR,
                )
        except ledger.LedgerError:
            taxable_left = 0  # the workspace is gone: nothing to adjust
    await record_audit(
        session,
        None,
        "billing.chargeback",
        tenant_id=payment.tenant_id,
        tenant_label=tenant_name,
        target_type="payment",
        target_id=str(payment.id),
        details={
            "payment_id": str(payment.id),
            "dispute_id": dispute_id,
            "status": "lost",
            "taxable_minor": taxable_left,
        },
    )
    return Processed(
        "done",
        attention=[
            _with_reason(
                attention,
                "A dispute was lost: the payment was taken back and the balance was adjusted.",
            )
        ],
    )


def _with_reason(item: AfterCommit, reason: str) -> AfterCommit:
    return AfterCommit(
        kind="attention",
        tenant_id=item.tenant_id,
        account_id=item.account_id,
        tenant_name=item.tenant_name,
        provider_payment_id=item.provider_payment_id,
        reason=reason,
    )


# ---------------------------------------------------------------------------- the sweep


async def sweep(
    session_factory: Any,
    provider: PaymentProvider,
    follow_up: Callable[[CaptureResult], Awaitable[None]],
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """One pass of the five-minute sweep: retry due events, expire stale orders, re-read payments
    still `authorized`, and re-read the open orders so a lost webhook loses nothing. Each unit of
    work commits on its own; a provider outage ends the pass early and is tried again next time."""
    moment = now or datetime.now(UTC)
    counts = {"events": 0, "expired": 0, "authorized": 0, "healed": 0, "refunds": 0}
    async with session_factory() as session:
        pending = list(
            (
                await session.execute(
                    select(PaymentEvent)
                    .where(PaymentEvent.status.in_(("pending", "failed")))
                    .order_by(PaymentEvent.received_at)
                    .limit(SWEEP_BATCH)
                )
            )
            .scalars()
            .all()
        )
    due = [
        e.event_id
        for e in pending
        if (e.status == "pending" and e.received_at <= moment - PENDING_AFTER)
        or (e.status == "failed" and e.attempts < MAX_ATTEMPTS and due_at(e) <= moment)
    ]
    for event_id in due:
        async with session_factory() as session:
            out = await process(session, provider, event_id, now=moment)
        counts["events"] += 1
        for result in out.results:
            await follow_up(result)
        if out.attention:
            await follow_up(capture.attention_result(out.attention))
    try:
        async with session_factory() as session:
            await _expire_orders(session, moment, counts)
            await session.commit()
        await _heal(session_factory, provider, follow_up, moment, counts)
        settled, notes = await refunds.settle_stale_pending(session_factory, provider, now=moment)
        counts["refunds"] = settled
        for note in notes:
            await follow_up(capture.attention_result([note]))
    except ProviderError as exc:
        logger.warning("Payment sweep stopped early (%s)", exc.kind)
    return counts


async def _expire_orders(session: AsyncSession, moment: datetime, counts: dict[str, int]) -> None:
    """An order still `created` after its own expiry (a link's, else 24 hours) is `expired`.
    A payment that arrives for it later is still credited (`apply_captured`)."""
    candidates = (
        (
            await session.execute(
                select(PaymentOrder)
                .where(PaymentOrder.status == "created", PaymentOrder.expires_at < moment)
                .order_by(PaymentOrder.expires_at)
                .limit(SWEEP_BATCH)
            )
        )
        .scalars()
        .all()
    )
    for order in candidates:
        order.status = "expired"
        counts["expired"] += 1


async def _heal(
    session_factory: Any,
    provider: PaymentProvider,
    follow_up: Callable[[CaptureResult], Awaitable[None]],
    moment: datetime,
    counts: dict[str, int],
) -> None:
    async with session_factory() as session:
        watched = list(
            (
                await session.execute(
                    select(Payment.provider_payment_id).where(
                        Payment.status == "authorized",
                        Payment.created_at > moment - AUTHORIZED_WATCH,
                    )
                )
            )
            .scalars()
            .all()
        )
        open_orders = list(
            (
                await session.execute(
                    select(PaymentOrder.provider_order_id).where(
                        PaymentOrder.status.in_(("created", "failed")),
                        PaymentOrder.provider_order_id.is_not(None),
                        PaymentOrder.created_at > moment - ORDER_LIFETIME,
                    )
                )
            )
            .scalars()
            .all()
        )
    for pay_id in watched:
        try:
            info = await provider.fetch_payment(pay_id)
        except ProviderError as exc:
            if exc.kind == "not_found":  # one unknown payment never stops the rest
                continue
            raise
        if info.status == "captured":
            await _apply_one(session_factory, info, follow_up, moment)
            counts["authorized"] += 1
    for order_id in open_orders:
        if order_id is None:
            continue
        try:
            found = await provider.payments_of_order(order_id)
        except ProviderError as exc:
            if exc.kind == "not_found":
                continue
            raise
        for info in found:
            if info.status == "captured" and await _apply_one(
                session_factory, info, follow_up, moment
            ):
                counts["healed"] += 1


async def _apply_one(
    session_factory: Any,
    info: Any,
    follow_up: Callable[[CaptureResult], Awaitable[None]],
    moment: datetime,
) -> bool:
    async with session_factory() as session:
        result = await capture.apply_captured(session, info, source="sweep", now=moment)
        await session.commit()
    if result.created:
        await follow_up(result)
    return result.created


def queue_event(event_id: str) -> None:
    """Wake a worker for a stored event. A broker outage is logged and swallowed: the event is
    stored, and the sweep hands it to a worker within minutes."""
    from callobuzz_rag.worker.payment_tasks import process_payment_event_task

    try:
        process_payment_event_task.apply_async(
            kwargs={"event_id": event_id}, queue="periodic", retry=False
        )
    except Exception:
        logger.error("Could not queue payment event %s; the sweep will", event_id, exc_info=True)
