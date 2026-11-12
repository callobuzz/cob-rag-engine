"""Refunds from unused balance on a real PostgreSQL (P1 T8). Needs Docker.

The balance entry is written and committed before the provider is called; a refusal puts the
money back; promotional credit is never paid out; two refunds racing for the same balance end
with one refund and one 409.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from callobuzz_rag.accounts.settings_store import read_engine_settings_row
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing import ledger
from callobuzz_rag.billing.payments import capture, documents, events, refunds
from callobuzz_rag.billing.payments.base import PaymentInfo, ProviderError, RefundInfo
from callobuzz_rag.billing.payments.fake import FakePaymentProvider
from callobuzz_rag.db.models.payment import Payment, PaymentRefund
from tests.integration.test_payments_capture_db import (
    Owner,
    apply,
    audits,
    balance,
    ledger_rows,
    make_owner,
    payment_of,
    start,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def factory(db_engine: Any) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def paid(
    f: async_sessionmaker[AsyncSession],
    fake: FakePaymentProvider,
    owner: Owner,
    amount: int = 100_000,
    *,
    purpose: str = "top_up",
) -> tuple[Payment, PaymentInfo]:
    order = await start(
        f, fake, owner, purpose=purpose, amount=None if purpose == "plan" else amount
    )
    info = fake.capture(order.provider_order_id or "")
    await apply(f, info)
    payment = await payment_of(f, info.provider_payment_id)
    assert payment is not None
    return payment, info


async def ask(
    f: async_sessionmaker[AsyncSession],
    payment: Payment,
    amount: int,
    *,
    key: str | None = None,
    confirm: int | None = None,
) -> tuple[PaymentRefund, bool]:
    async with f() as session:
        engine = (await read_engine_settings_row(session))[0]
        refund, created = await refunds.request_refund(
            session,
            engine,
            payment.id,
            amount_minor=amount,
            memo="Customer cancelled",
            confirm_amount_minor=confirm,
            idempotency_key=key or f"rf-{uuid.uuid4().hex[:20]}",
            actor=("admin_key", None, "admin key"),
        )
        await session.commit()
        return refund, created


async def refund_row(f: async_sessionmaker[AsyncSession], refund_id: uuid.UUID) -> PaymentRefund:
    async with f() as session:
        row = await session.get(PaymentRefund, refund_id)
        assert row is not None
        return row


async def test_refundable_excludes_promotional_credit(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    payment, _ = await paid(factory, fake, owner)  # ₹1,000 paid
    async with factory() as session:
        await ledger.append_entries(
            session,
            owner.account_id,
            [
                ledger.NewEntry(ledger.EntryType.ADJUSTMENT, -30_000, "fee-300", memo="fee"),
                ledger.NewEntry(
                    ledger.EntryType.INCLUDED_CREDIT,
                    30_000,
                    "promo-300",
                    expires_at=datetime.now(UTC) + timedelta(days=20),
                ),
            ],
            actor=ledger.SYSTEM_ACTOR,
        )
        await session.commit()
    assert await balance(factory, owner) == (100_000, 100_000)
    async with factory() as session:
        from callobuzz_rag.db.models.billing_account import BillingAccount

        account = await session.get(BillingAccount, owner.account_id)
        stored = await session.get(Payment, payment.id)
        assert account is not None and stored is not None
        assert (
            await refunds.refundable_minor(session, account, stored) == 70_000
        )  # ₹1,000 - ₹300 promo


async def test_a_partly_refunded_payment_has_less_left(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    payment, _ = await paid(factory, fake, owner)
    await ask(factory, payment, 40_000)
    async with factory() as session:
        from callobuzz_rag.db.models.billing_account import BillingAccount

        account = await session.get(BillingAccount, owner.account_id)
        stored = await session.get(Payment, payment.id)
        assert account is not None and stored is not None
        assert await refunds.refundable_minor(session, account, stored) == 60_000


async def test_over_the_refundable_is_409_with_the_amount_left(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    payment, _ = await paid(factory, fake, owner)
    with pytest.raises(APIError) as err:
        await ask(factory, payment, 100_001)
    assert err.value.status_code == 409 and err.value.code == "refund_exceeds_refundable"
    assert err.value.extra["refundable_minor"] == 100_000


async def test_a_payment_older_than_the_refund_window_is_refused(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    payment, _ = await paid(factory, fake, owner)
    async with factory() as session:
        row = await session.get(Payment, payment.id)
        assert row is not None
        row.captured_at = datetime.now(UTC) - timedelta(days=refunds.REFUND_WINDOW_DAYS + 5)
        await session.commit()
    with pytest.raises(APIError) as err:
        await ask(factory, payment, 10_000)
    assert err.value.code == "refund_window_closed"
    assert await balance(factory, owner) == (100_000, 100_000)


async def test_the_balance_drops_before_the_provider_is_called(factory: Any) -> None:
    seen: dict[str, Any] = {}

    class Spy(FakePaymentProvider):
        async def refund(self, provider_payment_id: str, **kw: Any) -> RefundInfo:
            seen["rows"] = [
                (r.entry_type, r.amount_minor) for r in await ledger_rows(factory, owner)
            ]
            seen["gross"] = kw["amount_minor"]
            return await super().refund(provider_payment_id, **kw)

    fake = Spy()
    owner = await make_owner(factory)
    payment, _ = await paid(factory, fake, owner, 99_900)  # ₹999 + GST, intra
    refund, _ = await ask(factory, payment, 50_000)
    assert (refund.cgst_minor, refund.sgst_minor, refund.gross_minor) == (4_500, 4_500, 59_000)
    refund, after = await refunds.send_refund(factory, fake, refund.id)
    assert seen["rows"][-1] == ("refund", -50_000)  # already on the books when the provider heard
    assert seen["gross"] == 59_000
    assert refund.status == "pending" and refund.provider_refund_id in fake.refunds
    assert after == []
    assert await balance(factory, owner) == (49_900, 49_900)


async def test_a_provider_refusal_puts_the_money_back(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    payment, _ = await paid(factory, fake, owner)
    fake.fail_refund_with = ProviderError("rejected", "no", status=400)
    refund, _ = await ask(factory, payment, 40_000)
    refund, after = await refunds.send_refund(factory, fake, refund.id)
    assert refund.status == "failed" and refund.reversal_entry_id is not None
    rows = await ledger_rows(factory, owner)
    assert [(r.entry_type, r.amount_minor, r.idempotency_key) for r in rows][-2:] == [
        ("refund", -40_000, f"refund:{refund.id}"),
        ("adjustment", 40_000, f"refund-failed:{refund.id}"),
    ]
    assert await balance(factory, owner) == (100_000, 100_000)
    assert after and after[0].kind == "attention"
    assert "billing.refund_failed" in [a.action for a in await audits(factory, owner)]
    # asking again for the same amount is possible: the failed refund counts for nothing
    again, created = await ask(factory, payment, 40_000)
    assert created and again.id != refund.id


async def test_refund_processed_event_settles_it_once(
    factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    payment, info = await paid(factory, fake, owner)
    called: list[uuid.UUID] = []

    async def credit_note(session: AsyncSession, refund: PaymentRefund) -> None:
        called.append(refund.id)

    monkeypatch.setattr(documents, "on_refund_processed", credit_note)
    refund, _ = await ask(factory, payment, 100_000)
    refund, _ = await refunds.send_refund(factory, fake, refund.id)
    fake.refunds[refund.provider_refund_id or ""] = RefundInfo(
        refund.provider_refund_id or "", info.provider_payment_id, 118_000, "processed"
    )
    hook = fake.event(
        "refund.processed", payment=info.provider_payment_id, refund=refund.provider_refund_id
    )
    async with factory() as session:
        stored = await events.store_event(
            session, events.FernetEncryptor(_key()), hook.parsed, hook.body
        )
        assert stored is not None
        await session.commit()
    async with factory() as session:
        out = await events.process(session, fake, hook.event_id)
    assert out.status == "done" and out.attention and out.attention[0].kind == "refund"
    settled = await refund_row(factory, refund.id)
    assert settled.status == "processed" and settled.processed_at is not None
    paid_row = await payment_of(factory, info.provider_payment_id)
    assert paid_row is not None and (paid_row.status, paid_row.refunded_minor) == (
        "refunded",
        118_000,
    )
    assert called == [refund.id]
    assert "billing.refund_issued" in [a.action for a in await audits(factory, owner)]
    # a replay of the same delivery, and a second delivery of the same fact, change nothing
    async with factory() as session:
        assert (await events.process(session, fake, hook.event_id)).status == "skipped"
    again = fake.event(
        "refund.processed", payment=info.provider_payment_id, refund=refund.provider_refund_id
    )
    async with factory() as session:
        await events.store_event(session, events.FernetEncryptor(_key()), again.parsed, again.body)
        await session.commit()
    async with factory() as session:
        out2 = await events.process(session, fake, again.event_id)
    assert out2.attention == [] and called == [refund.id]


def _key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


async def test_refund_failed_event_after_pending_reverses_once(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    payment, info = await paid(factory, fake, owner)
    refund, _ = await ask(factory, payment, 50_000)
    refund, _ = await refunds.send_refund(factory, fake, refund.id)
    fake.refunds[refund.provider_refund_id or ""] = RefundInfo(
        refund.provider_refund_id or "", info.provider_payment_id, 59_000, "failed"
    )
    for _ in range(2):  # the same fact twice: the second finds it settled
        async with factory() as session:
            out = await refunds.settle_event(
                session,
                fake,
                "refund.failed",
                {"refund": refund.provider_refund_id or "", "payment": info.provider_payment_id},
                now=datetime.now(UTC),
            )
            await session.commit()
        assert out.status == "done"
    rows = await ledger_rows(factory, owner)
    assert sum(1 for r in rows if r.idempotency_key == f"refund-failed:{refund.id}") == 1
    assert await balance(factory, owner) == (100_000, 100_000)
    assert (await refund_row(factory, refund.id)).status == "failed"


async def test_a_large_refund_needs_the_amount_typed_again(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    payment, _ = await paid(factory, fake, owner, amount=2_000_000)  # ₹20,000
    with pytest.raises(APIError) as err:
        await ask(factory, payment, 1_500_000)  # ₹15,000 is over the ₹10,000 default limit
    assert err.value.status_code == 422 and err.value.code == "confirm_mismatch"
    with pytest.raises(APIError):
        await ask(factory, payment, 1_500_000, confirm=1_400_000)
    refund, created = await ask(factory, payment, 1_500_000, confirm=1_500_000)
    assert created and refund.status == "requested"


async def test_the_same_idempotency_key_is_the_same_refund(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    payment, _ = await paid(factory, fake, owner)
    first, created = await ask(factory, payment, 30_000, key="same-key-0001")
    again, created_again = await ask(factory, payment, 30_000, key="same-key-0001")
    assert created and not created_again and again.id == first.id
    assert sum(1 for r in await ledger_rows(factory, owner) if r.entry_type == "refund") == 1
    with pytest.raises(ledger.IdempotencyConflict):
        await ask(factory, payment, 31_000, key="same-key-0001")


async def test_two_refunds_racing_for_one_balance_end_with_one(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    payment, _ = await paid(factory, fake, owner)  # ₹1,000

    async def attempt() -> str:
        try:
            await ask(factory, payment, 60_000)
        except APIError as exc:
            return exc.code
        return "ok"

    results = await asyncio.gather(attempt(), attempt())
    assert sorted(results) == ["ok", "refund_exceeds_refundable"]
    assert await balance(factory, owner) == (40_000, 40_000)
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(PaymentRefund).where(PaymentRefund.payment_id == payment.id)
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1


async def test_the_sweep_settles_a_refund_whose_webhook_came_too_early(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    payment, info = await paid(factory, fake, owner)
    refund, _ = await ask(factory, payment, 100_000)
    refund, _ = await refunds.send_refund(factory, fake, refund.id)
    fake.refunds[refund.provider_refund_id or ""] = RefundInfo(
        refund.provider_refund_id or "", info.provider_payment_id, 118_000, "processed"
    )
    notes: list[capture.CaptureResult] = []

    async def follow_up(result: capture.CaptureResult) -> None:
        notes.append(result)

    later = datetime.now(UTC) + timedelta(minutes=20)
    counts = await events.sweep(factory, fake, follow_up, now=later)
    assert counts["refunds"] >= 1
    assert (await refund_row(factory, refund.id)).status == "processed"
    assert any(i.kind == "refund" for r in notes for i in r.after)


async def test_an_unreachable_provider_keeps_the_refund_requested_and_the_balance_down(
    factory: Any,
) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    payment, _ = await paid(factory, fake, owner)
    fake.fail_refund_with = ProviderError("unreachable", "ReadTimeout")
    refund, _ = await ask(factory, payment, 40_000)
    refund, after = await refunds.send_refund(factory, fake, refund.id)
    assert refund.status == "requested" and refund.reversal_entry_id is None and after == []
    rows = await ledger_rows(factory, owner)
    assert [r.entry_type for r in rows][-1] == "refund"  # no refund-failed adjustment
    assert await balance(factory, owner) == (60_000, 60_000)


async def test_the_sweep_sends_a_requested_refund_once_even_if_the_first_try_got_through(
    factory: Any,
) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    payment, _ = await paid(factory, fake, owner)
    fake.fail_refund_with = ProviderError("unreachable", "ReadTimeout")
    fake.fail_refund_after_create = True  # the provider made it; only the answer was lost
    refund, _ = await ask(factory, payment, 40_000)
    refund, _ = await refunds.send_refund(factory, fake, refund.id)
    mine = payment.provider_payment_id
    assert refund.status == "requested" and len(fake.refund_receipts) == 1
    fake.fail_refund_with = None

    async def follow_up(result: capture.CaptureResult) -> None:
        return None

    later = datetime.now(UTC) + timedelta(minutes=20)
    await events.sweep(factory, fake, follow_up, now=later)
    row = await refund_row(factory, refund.id)
    assert row.status == "pending" and row.provider_refund_id in fake.refunds
    # adopted by its receipt, never paid twice (other tests' leftovers may be swept too)
    assert [k for k in fake.refund_receipts if k[0] == mine] == [(mine, str(refund.id))]
    assert len([r for r in fake.refunds.values() if r.provider_payment_id == mine]) == 1
    assert await balance(factory, owner) == (60_000, 60_000)


async def test_the_sweep_sends_a_refund_whose_send_never_ran(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    payment, _ = await paid(factory, fake, owner)
    refund, _ = await ask(factory, payment, 40_000)  # committed, then the process died

    async def follow_up(result: capture.CaptureResult) -> None:
        return None

    early = datetime.now(UTC) + timedelta(minutes=1)
    await events.sweep(factory, fake, follow_up, now=early)
    assert (await refund_row(factory, refund.id)).status == "requested"  # too fresh to touch
    later = datetime.now(UTC) + timedelta(minutes=20)
    await events.sweep(factory, fake, follow_up, now=later)
    row = await refund_row(factory, refund.id)
    mine = payment.provider_payment_id
    assert row.status == "pending"
    assert len([r for r in fake.refunds.values() if r.provider_payment_id == mine]) == 1
