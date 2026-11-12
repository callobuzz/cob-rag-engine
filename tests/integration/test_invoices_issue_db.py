"""P2 on a real PostgreSQL: the invoice and the credit note are written in the capture's and the
refund's own transaction, with the real ledger and locks. Needs Docker.

The schema is migrated to every head in its own container, so the file
does not need the session fixture (which upgrades to a single `head`).
"""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from callobuzz_rag.billing import reconcile
from callobuzz_rag.billing.payments import documents as payment_documents
from callobuzz_rag.billing.payments.fake import FakePaymentProvider
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.payment import Payment
from callobuzz_rag.db.models.tax_document import TaxDocument, TaxDocumentCounter
from tests.integration.conftest import _async_url
from tests.integration.test_accounts_flow import _alembic
from tests.integration.test_payments_capture_db import apply, make_owner, start

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "heads")
        yield url


@pytest_asyncio.fixture
async def factory(pg_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(pg_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def documents_of(factory: Any, tenant_id: Any) -> list[TaxDocument]:
    async with factory() as session:
        return list(
            (
                await session.execute(
                    select(TaxDocument)
                    .where(TaxDocument.tenant_id == tenant_id)
                    .order_by(TaxDocument.issued_at, TaxDocument.number)
                )
            )
            .scalars()
            .all()
        )


async def test_capture_writes_the_invoice_in_its_transaction(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    info = fake.capture(order.provider_order_id or "", method="upi")
    result = await apply(factory, info)
    assert result.status == "credited"
    [doc] = await documents_of(factory, owner.tenant_id)
    assert doc.doc_type == "invoice" and doc.number.startswith("INV/")
    assert (doc.taxable_minor, doc.cgst_minor, doc.sgst_minor, doc.total_minor) == (
        100_000,
        9_000,
        9_000,
        118_000,
    )
    assert doc.pdf_key is None and doc.buyer["legal_name"] == "Sharma Furniture"
    async with factory() as session:
        actions = (
            (
                await session.execute(
                    select(AuditLog.action).where(AuditLog.tenant_id == owner.tenant_id)
                )
            )
            .scalars()
            .all()
        )
        assert "billing.invoice_issued" in actions


async def test_replay_and_race_issue_one_invoice(factory: Any) -> None:
    import asyncio

    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner, amount=50_000)
    info = fake.capture(order.provider_order_id or "")
    await asyncio.gather(
        apply(factory, info, "checkout"), apply(factory, info), apply(factory, info)
    )
    assert len(await documents_of(factory, owner.tenant_id)) == 1


async def test_a_failure_in_issue_rolls_back_the_payment_and_the_number(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    info = fake.capture(order.provider_order_id or "")
    async with factory() as session:
        before = (
            await session.execute(select(func.coalesce(func.max(TaxDocumentCounter.last_seq), 0)))
        ).scalar_one()
    with (
        patch.object(
            payment_documents,
            "on_payment_captured",
            AsyncMock(side_effect=RuntimeError("renderer exploded")),
        ),
        pytest.raises(RuntimeError),
    ):
        await apply(factory, info)
    async with factory() as session:
        payments = (
            await session.execute(
                select(func.count())
                .select_from(Payment)
                .where(Payment.provider_payment_id == info.provider_payment_id)
            )
        ).scalar_one()
        after = (
            await session.execute(select(func.coalesce(func.max(TaxDocumentCounter.last_seq), 0)))
        ).scalar_one()
    assert payments == 0 and after == before
    # the webhook is retried: now it works, with the very next number
    assert (await apply(factory, info)).status == "credited"
    [doc] = await documents_of(factory, owner.tenant_id)
    assert doc.number.endswith(f"{before + 1:05d}")


async def test_r7_finds_a_payment_without_an_invoice(factory: Any) -> None:
    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    info = fake.capture(order.provider_order_id or "")
    with patch.object(payment_documents, "on_payment_captured", AsyncMock()):  # as before P2
        await apply(factory, info)
    async with factory() as session:
        found = await reconcile.document_findings(session, datetime.now(UTC) + timedelta(minutes=5))
    mine = [f for f in found if f.tenant_id == owner.tenant_id]
    assert [f.check for f in mine] == ["R7a"]


async def test_processed_refund_writes_a_credit_note_against_the_invoice(factory: Any) -> None:
    from cryptography.fernet import Fernet

    from callobuzz_rag.billing.payments import events, refunds
    from callobuzz_rag.billing.payments.base import RefundInfo
    from tests.integration.test_payments_refunds_db import ask, paid

    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    payment, info = await paid(factory, fake, owner, 100_000)
    refund, _ = await ask(factory, payment, 50_000)
    refund, _ = await refunds.send_refund(factory, fake, refund.id)
    fake.refunds[refund.provider_refund_id or ""] = RefundInfo(
        refund.provider_refund_id or "", info.provider_payment_id, 59_000, "processed"
    )
    hook = fake.event(
        "refund.processed", payment=info.provider_payment_id, refund=refund.provider_refund_id
    )
    async with factory() as session:
        encryptor = events.FernetEncryptor(Fernet.generate_key().decode())
        await events.store_event(session, encryptor, hook.parsed, hook.body)
        await session.commit()
    async with factory() as session:
        assert (await events.process(session, fake, hook.event_id)).status == "done"
    invoice, note = await documents_of(factory, owner.tenant_id)
    assert invoice.doc_type == "invoice" and note.doc_type == "credit_note"
    assert note.number.startswith("CN/") and note.original_id == invoice.id
    assert (note.taxable_minor, note.cgst_minor, note.sgst_minor) == (-50_000, -4_500, -4_500)
    assert note.refund_id == refund.id
    # the same fact delivered again changes nothing
    async with factory() as session:
        assert (await events.process(session, fake, hook.event_id)).status == "skipped"
    assert len(await documents_of(factory, owner.tenant_id)) == 2


async def test_a_failed_refund_issues_no_credit_note(factory: Any) -> None:
    from callobuzz_rag.billing.invoicing.documents import credit_note_of_refund
    from callobuzz_rag.billing.payments import refunds
    from callobuzz_rag.billing.payments.base import ProviderError
    from callobuzz_rag.db.models.payment import PaymentRefund
    from tests.integration.test_payments_refunds_db import ask, paid

    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    payment, _ = await paid(factory, fake, owner, 100_000)
    fake.fail_refund_with = ProviderError("rejected", "no", status=400)
    refund, _ = await ask(factory, payment, 40_000)
    refund, _ = await refunds.send_refund(factory, fake, refund.id)
    assert refund.status == "failed"
    async with factory() as session:
        assert await credit_note_of_refund(session, refund.id) is None
        stored = await session.get(PaymentRefund, refund.id)
        assert stored is not None and stored.credit_note_skipped is None
    assert [d.doc_type for d in await documents_of(factory, owner.tenant_id)] == ["invoice"]


async def test_two_runs_on_one_document_upload_once_and_mail_once(factory: Any) -> None:
    import asyncio
    from types import SimpleNamespace

    from callobuzz_rag.billing.invoicing import store
    from callobuzz_rag.db.models.payment import BillingProfile
    from tests.unit.test_invoice_render_task import Mailbox, MemoryStorage

    class SlowStorage(MemoryStorage):
        async def upload(
            self, bucket: str, key: str, data: Any, content_type: str | None = None
        ) -> None:
            await asyncio.sleep(0.2)  # the second runner arrives while this one is writing
            await super().upload(bucket, key, data, content_type)

    fake = FakePaymentProvider()
    owner = await make_owner(factory)
    order = await start(factory, fake, owner)
    await apply(factory, fake.capture(order.provider_order_id or ""))
    [doc] = await documents_of(factory, owner.tenant_id)
    async with factory() as session:
        profile = (
            await session.execute(
                select(BillingProfile).where(BillingProfile.tenant_id == owner.tenant_id)
            )
        ).scalar_one()
        profile.email_for_invoices = "accounts@sharma.example"
        await session.commit()
    storage, mail = SlowStorage(), Mailbox()
    settings = SimpleNamespace(s3_bucket="rag", dashboard_url="https://app.example.com")
    outcomes = await asyncio.gather(
        *(
            store.render_and_store(settings, factory, doc.id, storage=storage, enqueue=mail)
            for _ in range(2)
        )
    )
    assert outcomes.count("stored") == 1  # the other run only mailed or kept
    assert storage.uploads == 1 and len(mail.sent) == 1
