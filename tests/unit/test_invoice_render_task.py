"""P2 T5: the PDF is written once to storage, hashed on the row, and mailed once."""

import hashlib
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
from callobuzz_rag.billing.invoicing import store
from callobuzz_rag.billing.invoicing.documents import issue_invoice
from callobuzz_rag.db.models.payment import BillingProfile
from callobuzz_rag.db.models.tax_document import TaxDocument
from tests.unit.accounts_fakes import FakeDB
from tests.unit.test_invoice_issue import NOW, paid, seed

SETTINGS = SimpleNamespace(s3_bucket="rag", dashboard_url="https://app.callobuzz.example")


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.uploads = 0
        self.down = False

    async def exists(self, bucket: str, key: str) -> bool:
        return (bucket, key) in self.objects

    async def download(self, bucket: str, key: str) -> bytes:
        return self.objects[(bucket, key)]

    async def upload(
        self, bucket: str, key: str, data: Any, content_type: str | None = None
    ) -> None:
        if self.down:
            raise ConnectionError("storage down")
        self.uploads += 1
        self.objects[(bucket, key)] = data.read()


class Mailbox:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, template: str, to: str, context: Any, *, expires_s: float) -> None:
        self.sent.append((template, to, dict(context)))


@pytest.fixture(autouse=True)
def _fresh_settings() -> Any:
    invalidate_engine_settings_cache()
    yield
    invalidate_engine_settings_cache()


async def issued(db: FakeDB, *, email: str | None = "accounts@sharma.example") -> TaxDocument:
    seed(db)
    payment, order = paid(db)
    profile = next(p for p in db.all(BillingProfile) if p.tenant_id == payment.tenant_id)
    profile.email_for_invoices = email
    document = await issue_invoice(db.session(), payment, order, now=NOW)
    document.created_at = NOW
    return document


async def test_writes_the_pdf_once_sets_the_row_and_mails_once() -> None:
    db, storage, mail = FakeDB(), MemoryStorage(), Mailbox()
    document = await issued(db)
    run = lambda: store.render_and_store(  # noqa: E731
        SETTINGS, db.factory(), document.id, storage=storage, enqueue=mail
    )
    assert await run() == "stored"
    assert await run() == "kept"
    key = f"billing/tax-documents/26-27/{document.id}.pdf"
    assert list(storage.objects) == [("rag", key)] and storage.uploads == 1
    data = storage.objects[("rag", key)]
    assert data.startswith(b"%PDF")
    assert document.pdf_key == key and document.pdf_sha256 == hashlib.sha256(data).hexdigest()
    assert document.emailed_at is not None
    [(template, to, context)] = mail.sent
    assert (template, to) == ("invoice_issued", "accounts@sharma.example")
    assert context["number"] == "INV/26-27/00001" and context["total"] == "₹1,180.00"
    assert context["invoices_url"] == "https://app.callobuzz.example/plan"


async def test_storage_down_sets_nothing_and_the_sweep_fills_it_later() -> None:
    db, storage, mail = FakeDB(), MemoryStorage(), Mailbox()
    document = await issued(db)
    storage.down = True
    with pytest.raises(ConnectionError):
        await store.render_and_store(
            SETTINGS, db.factory(), document.id, storage=storage, enqueue=mail
        )
    assert document.pdf_key is None and document.pdf_sha256 is None and mail.sent == []

    # too young for the sweep, then storage is back and a minute has passed
    assert await store.due_documents(db.factory(), NOW + timedelta(seconds=30)) == []
    storage.down = False
    counts = await store.sweep(
        SETTINGS, db.factory(), storage=storage, enqueue=mail, now=NOW + timedelta(minutes=2)
    )
    assert counts == {"stored": 1, "mailed": 0, "failed": 0}
    assert document.pdf_key is not None and len(mail.sent) == 1
    again = await store.sweep(
        SETTINGS, db.factory(), storage=storage, enqueue=mail, now=NOW + timedelta(minutes=12)
    )
    assert again == {"stored": 0, "mailed": 0, "failed": 0} and len(mail.sent) == 1


async def test_a_pdf_that_was_uploaded_but_never_recorded_is_kept_not_overwritten() -> None:
    db, storage, mail = FakeDB(), MemoryStorage(), Mailbox()
    document = await issued(db)
    key = f"billing/tax-documents/26-27/{document.id}.pdf"
    storage.objects[("rag", key)] = b"%PDF-earlier"
    await store.render_and_store(SETTINGS, db.factory(), document.id, storage=storage, enqueue=mail)
    assert storage.uploads == 0 and storage.objects[("rag", key)] == b"%PDF-earlier"
    assert document.pdf_sha256 == hashlib.sha256(b"%PDF-earlier").hexdigest()


async def test_without_an_invoice_address_the_owners_are_mailed() -> None:
    from callobuzz_rag.db.models.tenant_member import TenantMember
    from callobuzz_rag.db.models.user import User

    db, storage, mail = FakeDB(), MemoryStorage(), Mailbox()
    document = await issued(db, email=None)
    user = db.add(User(email="asha@sharma.example", password_hash="x", is_active=True))
    db.add(TenantMember(tenant_id=document.tenant_id, user_id=user.id, role="owner"))
    await store.render_and_store(SETTINGS, db.factory(), document.id, storage=storage, enqueue=mail)
    assert [(t, to) for t, to, _ in mail.sent] == [("invoice_issued", "asha@sharma.example")]


async def test_no_recipient_stamps_the_row_so_the_sweep_stops() -> None:
    db, storage, mail = FakeDB(), MemoryStorage(), Mailbox()
    document = await issued(db, email=None)
    await store.render_and_store(SETTINGS, db.factory(), document.id, storage=storage, enqueue=mail)
    assert mail.sent == [] and document.emailed_at is not None


async def test_credit_note_pdf_names_the_original_and_the_refund() -> None:
    from callobuzz_rag.billing.invoicing.documents import issue_credit_note
    from callobuzz_rag.db.models.payment import Payment
    from tests.unit.test_invoice_credit_notes import refund_of

    db, storage, mail = FakeDB(), MemoryStorage(), Mailbox()
    invoice = await issued(db)
    payment = next(p for p in db.all(Payment) if p.id == invoice.payment_id)
    refund = refund_of(db, payment, 40_000)
    refund.provider_refund_id = "rfnd_Nabc"
    note = await issue_credit_note(db.session(), refund, now=NOW)
    assert note is not None
    await store.render_and_store(SETTINGS, db.factory(), note.id, storage=storage, enqueue=mail)
    import pypdfium2 as pdfium

    pdf = storage.objects[("rag", f"billing/tax-documents/26-27/{note.id}.pdf")]
    document = pdfium.PdfDocument(pdf)
    text = document[0].get_textpage().get_text_range()
    assert "Against invoice: INV/26-27/00001" in text and "rfnd_Nabc" in text
