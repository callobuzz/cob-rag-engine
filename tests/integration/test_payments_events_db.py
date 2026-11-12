"""Stored payment events and the sweep on a real PostgreSQL (P1 T7). Needs Docker.

The webhook only names entities; the worker fetches the truth from the provider (the fake here),
so a forged amount, an event arriving out of order or a delivery lost for a day all end the same
way: the money is credited once, or not at all.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from callobuzz_rag.billing.payments import capture, events
from callobuzz_rag.billing.payments.base import ProviderError
from callobuzz_rag.billing.payments.fake import FakePaymentProvider, FakeWebhook
from callobuzz_rag.core.secrets import FernetEncryptor
from callobuzz_rag.db.models.payment import Payment, PaymentEvent, PaymentOrder
from tests.integration.test_payments_capture_db import (  # noqa: F401  (fixtures and helpers)
    Owner,
    audits,
    balance,
    ledger_rows,
    make_owner,
    payment_of,
    start,
)

pytestmark = pytest.mark.integration

ENC = FernetEncryptor(Fernet.generate_key().decode())


@pytest.fixture
def factory(db_engine: Any) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def store(f: async_sessionmaker[AsyncSession], hook: FakeWebhook) -> None:
    async with f() as session:
        row = await events.store_event(session, ENC, hook.parsed, hook.body)
        assert row is not None
        await session.commit()


async def run(
    f: async_sessionmaker[AsyncSession], fake: FakePaymentProvider, hook: FakeWebhook, **kw: Any
) -> capture.Processed:
    async with f() as session:
        return await events.process(session, fake, hook.event_id, **kw)


async def event_row(f: async_sessionmaker[AsyncSession], event_id: str) -> PaymentEvent:
    async with f() as session:
        return (
            await session.execute(select(PaymentEvent).where(PaymentEvent.event_id == event_id))
        ).scalar_one()


async def test_duplicate_deliveries_store_once(factory: Any) -> None:
    fake = FakePaymentProvider()
    hook = fake.event("payment.captured", payment="pay_x")
    async with factory() as session:
        first = await events.store_event(session, ENC, hook.parsed, hook.body)
        second = await events.store_event(session, ENC, hook.parsed, hook.body)
        await session.commit()
    assert first is not None and second is None
    assert "asha@shree.example" not in (await event_row(factory, hook.event_id)).payload_encrypted


async def test_a_refund_for_a_refund_we_never_started_is_ignored(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    pay = fake.capture(order.provider_order_id or "")
    hook = fake.event("refund.processed", payment=pay.provider_payment_id, refund="rfnd_unknown")
    await store(factory, hook)
    out = await run(factory, fake, hook)
    assert out.status == "ignored"
    assert (await event_row(factory, hook.event_id)).status == "ignored"
    assert await ledger_rows(factory, owner) == []


async def test_captured_event_uses_the_api_truth_not_the_payload(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    pay = fake.capture(order.provider_order_id or "")
    forged = fake.event("payment.captured", payment=pay.provider_payment_id, amount_override=1)
    await store(factory, forged)
    await run(factory, fake, forged)
    assert await balance(factory, owner) == (100_000, 100_000)  # from fetch_payment, never the body
    assert fake.api_calls.count("fetch_payment") == 1


async def test_order_paid_and_payment_captured_credit_once(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    pay = fake.capture(order.provider_order_id or "")
    hooks = [
        fake.event("order.paid", payment=pay.provider_payment_id, order=order.provider_order_id),
        fake.event("payment.captured", payment=pay.provider_payment_id),
    ]
    for hook in hooks:
        await store(factory, hook)
    for hook in hooks:
        await run(factory, fake, hook)
    assert len(await ledger_rows(factory, owner)) == 1
    assert [(await event_row(factory, h.event_id)).status for h in hooks] == ["done", "done"]


async def test_failed_then_captured_in_either_order(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    bad = fake.fail(order.provider_order_id or "")
    good = fake.capture(order.provider_order_id or "")
    failed_hook = fake.event("payment.failed", payment=bad.provider_payment_id)
    ok_hook = fake.event("payment.captured", payment=good.provider_payment_id)
    # the success arrives first, the failure of the earlier attempt after it
    for hook in (ok_hook, failed_hook):
        await store(factory, hook)
    await run(factory, fake, ok_hook)
    await run(factory, fake, failed_hook)
    assert await balance(factory, owner) == (100_000, 100_000)
    async with factory() as session:
        stored = await session.get(PaymentOrder, order.id)
        assert stored is not None and stored.status == "paid"  # the failure never un-pays it
    failed_payment = await payment_of(factory, bad.provider_payment_id)
    assert failed_payment is not None and failed_payment.status == "failed"


async def test_a_failure_alone_marks_the_order_failed_and_a_later_capture_still_credits(
    factory: Any,
) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    bad = fake.fail(order.provider_order_id or "")
    hook = fake.event("payment.failed", payment=bad.provider_payment_id)
    await store(factory, hook)
    await run(factory, fake, hook)
    async with factory() as session:
        stored = await session.get(PaymentOrder, order.id)
        assert stored is not None and stored.status == "failed"
    good = fake.capture(order.provider_order_id or "")
    ok = fake.event("payment.captured", payment=good.provider_payment_id)
    await store(factory, ok)
    await run(factory, fake, ok)
    assert await balance(factory, owner) == (100_000, 100_000)


async def test_an_unknown_event_type_is_ignored_not_failed(factory: Any) -> None:
    fake = FakePaymentProvider()
    hook = fake.event("subscription.charged")
    await store(factory, hook)
    assert (await run(factory, fake, hook)).status == "ignored"
    assert (await event_row(factory, hook.event_id)).status == "ignored"


async def test_provider_down_fails_then_the_sweep_retries_with_backoff(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    pay = fake.capture(order.provider_order_id or "")
    hook = fake.event("payment.captured", payment=pay.provider_payment_id)
    await store(factory, hook)
    fake.down = ProviderError("unreachable", "down")
    t0 = datetime.now(UTC)
    out = await run(factory, fake, hook, now=t0)
    row = await event_row(factory, hook.event_id)
    assert out.status == "failed" and row.status == "failed" and row.attempts == 1
    assert row.error == "ProviderError:unreachable"
    assert await ledger_rows(factory, owner) == []
    sent: list[capture.CaptureResult] = []

    async def follow_up(result: capture.CaptureResult) -> None:
        sent.append(result)

    # too early for the one-minute back-off: untouched
    counts = await events.sweep(factory, fake, follow_up, now=t0 + timedelta(seconds=30))
    assert (await event_row(factory, hook.event_id)).attempts == 1  # not retried yet
    # the provider is back and the back-off has passed
    fake.down = None
    counts = await events.sweep(factory, fake, follow_up, now=t0 + timedelta(minutes=2))
    assert counts["events"] >= 1  # the database is shared: other tests may have due events
    assert (await event_row(factory, hook.event_id)).status == "done"
    assert await balance(factory, owner) == (100_000, 100_000)
    assert [r.status for r in sent] == ["credited"]


async def test_the_sixth_failure_tells_the_operator(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    pay = fake.capture(order.provider_order_id or "")
    hook = fake.event("payment.captured", payment=pay.provider_payment_id)
    await store(factory, hook)
    fake.down = ProviderError("unreachable", "down")
    last: capture.Processed | None = None
    for attempt in range(events.MAX_ATTEMPTS):
        last = await run(factory, fake, hook, now=datetime.now(UTC) + timedelta(days=attempt))
    assert last is not None and last.status == "failed" and last.attention
    assert "failed 6 times" in last.attention[0].reason
    assert (await event_row(factory, hook.event_id)).attempts == events.MAX_ATTEMPTS


async def test_a_payment_for_an_unknown_order_is_reported_not_credited(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    pay = fake.capture(order.provider_order_id or "")
    from dataclasses import replace

    fake.payments[pay.provider_payment_id] = replace(
        pay, provider_order_id="order_unknown", notes={}
    )
    hook = fake.event("payment.captured", payment=pay.provider_payment_id)
    await store(factory, hook)
    out = await run(factory, fake, hook)
    assert out.results[0].status == "unknown_order"
    assert out.results[0].after[0].kind == "attention"
    assert await ledger_rows(factory, owner) == []


async def test_a_lost_dispute_adjusts_the_balance_and_is_audited(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    pay = fake.capture(order.provider_order_id or "")
    ok = fake.event("payment.captured", payment=pay.provider_payment_id)
    await store(factory, ok)
    await run(factory, fake, ok)
    created = fake.event(
        "payment.dispute.created", payment=pay.provider_payment_id, dispute="disp_1"
    )
    lost = fake.event("payment.dispute.lost", payment=pay.provider_payment_id, dispute="disp_1")
    won = fake.event("payment.dispute.won", payment=pay.provider_payment_id, dispute="disp_2")
    for hook in (created, lost, won):
        await store(factory, hook)
    opened = await run(factory, fake, created)
    assert opened.attention and "dispute" in opened.attention[0].reason
    assert await balance(factory, owner) == (100_000, 100_000)  # nothing moves on "created"
    await run(factory, fake, lost)
    rows = await ledger_rows(factory, owner)
    assert [(r.entry_type, r.amount_minor, r.idempotency_key) for r in rows][-1] == (
        "adjustment",
        -100_000,
        "rzp-dispute:disp_1",
    )
    assert await balance(factory, owner) == (0, 0)
    [chargeback] = [a for a in await audits(factory, owner) if a.action == "billing.chargeback"]
    assert chargeback.details["status"] == "lost" and chargeback.details["dispute_id"] == "disp_1"
    await run(factory, fake, won)  # a won dispute changes nothing
    assert await balance(factory, owner) == (0, 0)


async def test_the_sweep_heals_a_webhook_that_never_came(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    fake.capture(order.provider_order_id or "")  # paid at the provider, no event stored
    sent: list[capture.CaptureResult] = []

    async def follow_up(result: capture.CaptureResult) -> None:
        sent.append(result)

    counts = await events.sweep(factory, fake, follow_up)
    assert counts["healed"] >= 1
    assert await balance(factory, owner) == (100_000, 100_000)
    [entry] = await ledger_rows(factory, owner)
    assert entry.entry_type == "top_up"
    assert any(r.status == "credited" for r in sent)
    again = await events.sweep(factory, fake, follow_up)  # nothing more to do, nothing doubled
    assert again["healed"] == 0 and len(await ledger_rows(factory, owner)) == 1


async def test_the_sweep_expires_old_orders_and_applies_a_late_capture_of_an_authorized_payment(
    factory: Any,
) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    stale = await start(factory, fake, owner)
    async with factory() as session:
        row = await session.get(PaymentOrder, stale.id)
        assert row is not None
        row.created_at = datetime.now(UTC) - timedelta(hours=30)
        row.expires_at = datetime.now(UTC) - timedelta(hours=6)
        await session.commit()
    watched = await start(factory, fake, owner, amount=50_000)
    auth = fake.authorize(watched.provider_order_id or "")
    await capture_via(factory, auth)
    # later the payment is captured at the provider; the sweep notices without any webhook
    from dataclasses import replace

    fake.payments[auth.provider_payment_id] = replace(
        auth, status="captured", captured_at=datetime.now(UTC)
    )

    async def follow_up(result: capture.CaptureResult) -> None:
        return None

    counts = await events.sweep(factory, fake, follow_up)
    assert counts["expired"] == 1 and counts["authorized"] == 1
    async with factory() as session:
        stale_row = await session.get(PaymentOrder, stale.id)
        assert stale_row is not None and stale_row.status == "expired"
    assert await balance(factory, owner) == (50_000, 50_000)


async def capture_via(f: async_sessionmaker[AsyncSession], info: Any) -> None:
    async with f() as session:
        await capture.apply_captured(session, info, source="webhook")
        await session.commit()


async def test_the_event_for_a_processed_payment_is_skipped_when_replayed(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    pay = fake.capture(order.provider_order_id or "")
    hook = fake.event("payment.captured", payment=pay.provider_payment_id)
    await store(factory, hook)
    assert (await run(factory, fake, hook)).status == "done"
    assert (await run(factory, fake, hook)).status == "skipped"  # done events are not run twice
    assert len(await ledger_rows(factory, owner)) == 1
    assert uuid.UUID(str(order.id))
    pay_row = await payment_of(factory, pay.provider_payment_id)
    assert isinstance(pay_row, Payment)


async def test_a_lost_dispute_without_a_dispute_entity_is_still_booked(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    pay = fake.capture(order.provider_order_id or "")
    ok = fake.event("payment.captured", payment=pay.provider_payment_id)
    await store(factory, ok)
    await run(factory, fake, ok)
    lost = fake.event("payment.dispute.lost", payment=pay.provider_payment_id)  # no dispute id
    await store(factory, lost)
    out = await run(factory, fake, lost)
    assert out.status == "done"
    assert (await event_row(factory, lost.event_id)).status == "done"
    assert await balance(factory, owner) == (0, 0)  # the capture's key was not reused


async def test_a_link_payment_without_notes_is_matched_by_the_signed_link_id(
    factory: Any,
) -> None:
    from callobuzz_rag.accounts.settings_store import read_engine_settings_row
    from callobuzz_rag.billing.payments import links

    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    async with factory() as session:
        settings = (await read_engine_settings_row(session))[0]
        order, _ = await links.create_link(
            session,
            fake,
            owner.tenant_id,
            settings,
            purpose="top_up",
            amount_minor=100_000,
            add_on_id=None,
            customer_name="Sharma Furniture",
            customer_email=None,
            created_by="admin",
        )
        await session.commit()
    assert order.link_id is not None
    pay = fake.capture_link(order.link_id)
    assert pay.notes == {} and pay.provider_order_id not in (None, order.provider_order_id)
    hook = fake.event(
        "payment_link.paid", payment=pay.provider_payment_id, payment_link=order.link_id
    )
    await store(factory, hook)
    out = await run(factory, fake, hook)
    assert [r.status for r in out.results] == ["credited"]
    assert await balance(factory, owner) == (100_000, 100_000)


async def test_open_payment_links_never_crowd_out_checkout_orders_from_expiry(
    factory: Any,
) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    old = datetime.now(UTC) - timedelta(hours=30)
    async with factory() as session:
        # more open links than a sweep batch, all older than a day but not yet expired
        template = await start(factory, fake, owner)
        row = await session.get(PaymentOrder, template.id)
        assert row is not None
        row.created_at = old
        row.expires_at = datetime.now(UTC) - timedelta(hours=6)
        await session.commit()
    async with factory() as session:
        for i in range(events.SWEEP_BATCH + 5):
            session.add(
                PaymentOrder(
                    **{
                        c.name: getattr(row, c.name)
                        for c in PaymentOrder.__table__.columns
                        if c.name not in ("id", "idempotency_key", "provider_order_id")
                    },
                    id=uuid.uuid4(),
                    idempotency_key=f"link-open-{i}-{uuid.uuid4().hex[:8]}",
                    provider_order_id=None,
                )
            )
        await session.flush()
        for o in (
            await session.execute(
                select(PaymentOrder).where(PaymentOrder.idempotency_key.like("link-open-%"))
            )
        ).scalars():
            o.channel = "link"
            o.expires_at = datetime.now(UTC) + timedelta(days=5)
        await session.commit()
    async with factory() as session:
        counts = {"expired": 0}
        await events._expire_orders(session, datetime.now(UTC), counts)
        await session.commit()
        stale = await session.get(PaymentOrder, template.id)
        assert stale is not None and stale.status == "expired"
