"""P2 T4: credit notes for refunds (on the in-memory database)."""

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
from callobuzz_rag.billing.invoicing import gst
from callobuzz_rag.billing.invoicing.documents import issue_credit_note, issue_invoice
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.payment import Payment, PaymentRefund
from callobuzz_rag.db.models.tax_document import TaxDocument
from tests.unit.accounts_fakes import FakeDB
from tests.unit.test_invoice_issue import NOW, paid, seed


@pytest.fixture(autouse=True)
def _fresh_settings() -> Any:
    invalidate_engine_settings_cache()
    yield
    invalidate_engine_settings_cache()


def refund_of(
    db: FakeDB, payment: Payment, taxable: int, already: gst.TaxSplit | None = None
) -> PaymentRefund:
    original = gst.TaxSplit(
        "intra" if payment.cgst_minor else "inter",
        payment.taxable_minor,
        payment.cgst_minor,
        payment.sgst_minor,
        payment.igst_minor,
        payment.gross_minor,
        "27",
    )
    split = gst.refund_split(original, taxable)
    return db.add(
        PaymentRefund(
            payment_id=payment.id,
            tenant_id=payment.tenant_id,
            account_id=payment.account_id,
            taxable_minor=split.taxable_minor,
            cgst_minor=split.cgst_minor,
            sgst_minor=split.sgst_minor,
            igst_minor=split.igst_minor,
            gross_minor=split.total_minor,
            status="processed",
            memo="Customer cancelled",
            requested_by="ops@callobuzz.example",
            idempotency_key=uuid.uuid4().hex,
            processed_at=NOW,
        )
    )


async def test_partial_refund_credit_note_mirrors_the_original_split() -> None:
    db = FakeDB()
    seed(db)
    payment, order = paid(db, taxable=99_900)
    invoice = await issue_invoice(db.session(), payment, order, now=NOW)
    assert invoice.number == "INV/26-27/00001"
    refund = refund_of(db, payment, 50_000)
    note = await issue_credit_note(db.session(), refund, now=NOW)
    assert note is not None
    assert (note.doc_type, note.number) == ("credit_note", "CN/26-27/00001")
    assert (note.taxable_minor, note.cgst_minor, note.sgst_minor, note.igst_minor) == (
        -50_000,
        -4_500,
        -4_500,
        0,
    )
    assert note.total_minor == -refund.gross_minor == -59_000
    assert (note.original_id, note.refund_id, note.payment_id) == (
        invoice.id,
        refund.id,
        payment.id,
    )
    assert note.kind == invoice.kind and note.place_of_supply == invoice.place_of_supply
    assert note.buyer == invoice.buyer and note.seller == invoice.seller
    assert note.lines[0]["taxable_minor"] == -50_000
    assert "INV/26-27/00001" in note.lines[0]["description"]
    [audit] = [a for a in db.all(AuditLog) if a.action == "billing.credit_note_issued"]
    assert audit.details["number"] == "CN/26-27/00001" and audit.details["total_minor"] == -59_000


async def test_a_full_refund_credits_the_whole_invoice() -> None:
    db = FakeDB()
    seed(db)
    payment, order = paid(db)
    invoice = await issue_invoice(db.session(), payment, order, now=NOW)
    note = await issue_credit_note(db.session(), refund_of(db, payment, 100_000), now=NOW)
    assert note is not None and note.total_minor == -invoice.total_minor


async def test_replayed_refund_event_issues_one_credit_note() -> None:
    db = FakeDB()
    seed(db)
    payment, order = paid(db)
    await issue_invoice(db.session(), payment, order, now=NOW)
    refund = refund_of(db, payment, 40_000)
    first = await issue_credit_note(db.session(), refund, now=NOW)
    second = await issue_credit_note(db.session(), refund, now=NOW)
    assert first is second
    assert len([d for d in db.all(TaxDocument) if d.doc_type == "credit_note"]) == 1


async def test_after_the_deadline_no_credit_note_and_the_reason_is_recorded() -> None:
    db = FakeDB()
    seed(db)
    payment, order = paid(db)
    await issue_invoice(db.session(), payment, order, now=NOW)  # 8 Oct 2026, FY 26-27
    refund = refund_of(db, payment, 40_000)
    late = datetime(2027, 12, 1, 5, 0, tzinfo=UTC)  # 1 December 2027, a day too late
    assert await issue_credit_note(db.session(), refund, now=late) is None
    assert refund.credit_note_skipped == "deadline"
    assert [d.doc_type for d in db.all(TaxDocument)] == ["invoice"]
    # asking again does not number anything either
    assert await issue_credit_note(db.session(), refund, now=NOW) is None


async def test_on_the_deadline_day_a_credit_note_is_still_issued() -> None:
    db = FakeDB()
    seed(db)
    payment, order = paid(db)
    await issue_invoice(db.session(), payment, order, now=NOW)
    last_day = datetime(2027, 11, 30, 17, 0, tzinfo=UTC)  # 22:30 IST on 30 November
    note = await issue_credit_note(db.session(), refund_of(db, payment, 40_000), now=last_day)
    assert note is not None and note.fy == "27-28" and note.number == "CN/27-28/00001"


async def test_a_payment_without_an_invoice_is_noted_not_guessed() -> None:
    db = FakeDB()
    seed(db)
    payment, _ = paid(db)
    refund = refund_of(db, payment, 40_000)
    assert await issue_credit_note(db.session(), refund, now=NOW) is None
    assert refund.credit_note_skipped == "no_invoice"


async def test_refunds_never_credit_more_than_the_invoice() -> None:
    from callobuzz_rag.billing.payments.refunds import _refund_split

    db = FakeDB()
    seed(db)
    payment, order = paid(db, taxable=99_901)  # odd value: every share rounds
    invoice = await issue_invoice(db.session(), payment, order, now=NOW)
    taken: gst.TaxSplit | None = None
    notes = []
    for part in (30_000, 30_000, payment.taxable_minor - 60_000):
        split = _refund_split(payment, part, taken)
        taken = gst.TaxSplit(
            "intra",
            (taken.taxable_minor if taken else 0) + split.taxable_minor,
            (taken.cgst_minor if taken else 0) + split.cgst_minor,
            (taken.sgst_minor if taken else 0) + split.sgst_minor,
            (taken.igst_minor if taken else 0) + split.igst_minor,
            (taken.total_minor if taken else 0) + split.total_minor,
            "27",
        )
        refund = db.add(
            PaymentRefund(
                payment_id=payment.id,
                tenant_id=payment.tenant_id,
                account_id=payment.account_id,
                taxable_minor=split.taxable_minor,
                cgst_minor=split.cgst_minor,
                sgst_minor=split.sgst_minor,
                igst_minor=split.igst_minor,
                gross_minor=split.total_minor,
                status="processed",
                memo="x",
                requested_by="ops@callobuzz.example",
                idempotency_key=uuid.uuid4().hex,
                processed_at=NOW,
            )
        )
        note = await issue_credit_note(db.session(), refund, now=NOW)
        assert note is not None
        notes.append(note)
    assert -sum(n.total_minor for n in notes) == invoice.total_minor
    assert -sum(n.taxable_minor for n in notes) == invoice.taxable_minor
    assert -sum(n.cgst_minor for n in notes) == invoice.cgst_minor
    assert -sum(n.sgst_minor for n in notes) == invoice.sgst_minor
    assert -sum(n.igst_minor for n in notes) == invoice.igst_minor


async def test_the_operator_is_told_when_a_refund_gets_no_credit_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from callobuzz_rag.accounts import lifecycle
    from callobuzz_rag.billing.payments import capture

    @asynccontextmanager
    async def factory() -> Any:
        yield object()

    async def owners(session: Any, tenant_id: Any) -> list[str]:
        return ["asha@shree.example"]

    monkeypatch.setattr(lifecycle, "owner_emails", owners)
    mail: list[tuple[str, str, dict[str, Any]]] = []
    item = capture.AfterCommit(
        kind="refund",
        tenant_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        tenant_name="Shree",
        gross_minor=59_000,
        reason="no credit note could be issued: the deadline has passed",
    )
    settings = SimpleNamespace(
        superadmin_email="ops@operator.example", dashboard_url="https://app.example.com"
    )
    await capture.run_after_commit(
        factory,
        settings,
        capture.CaptureResult(True, "credited", after=(item,)),
        enqueue=lambda template, to, ctx, **kw: mail.append((template, to, dict(ctx))),
        dispatch=AsyncMock(),
    )
    assert [(m[0], m[1]) for m in mail] == [
        ("refund_issued", "asha@shree.example"),
        ("payment_attention", "ops@operator.example"),
    ]
    assert "deadline" in mail[1][2]["reason"]
