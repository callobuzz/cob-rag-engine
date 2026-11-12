"""P2 T3: the tax invoice a captured payment gets (on the in-memory database).

`issue_invoice` is called directly with rows built like the capture builds them. That it runs in
the capture's own transaction, and its numbering under concurrency, are proved on PostgreSQL in
`tests/integration/test_invoices_issue_db.py` and `test_invoice_numbering_db.py`.
"""

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest

from callobuzz_rag.accounts.settings_store import (
    SETTINGS_ROW_ID,
    invalidate_engine_settings_cache,
)
from callobuzz_rag.billing.invoicing import gst
from callobuzz_rag.billing.invoicing.documents import (
    buyer_snapshot,
    credit_note_deadline,
    invoice_of_payment,
    issue_invoice,
)
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.payment import BillingProfile, Payment, PaymentOrder
from callobuzz_rag.db.models.tax_document import TaxDocument
from tests.unit.accounts_fakes import FakeDB

NOW = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
SELLER = {
    "company": {"legal_name": "Callobuzz", "state_code": "27", "city": "Pune"},
    "invoicing": {"gstin": "27AAPFU0939F1ZV", "invoice_prefix": "INV"},
}


@pytest.fixture(autouse=True)
def _fresh_settings() -> Any:
    invalidate_engine_settings_cache()
    yield
    invalidate_engine_settings_cache()


def seed(db: FakeDB, value: dict[str, Any] | None = SELLER) -> None:
    if value is not None:
        db.add(EngineSettingsRow(id=SETTINGS_ROW_ID, value=value))


def paid(
    db: FakeDB,
    *,
    kind: gst.Kind = "intra",
    place: str = "27",
    taxable: int = 100_000,
    description: str = "Prepaid balance top-up",
    sac: str = "998315",
    buyer_state: str = "27",
    gstin: str | None = "27AAACB2230M1ZP",
    country: str = "IN",
) -> tuple[Payment, PaymentOrder]:
    split = gst.split_exclusive(taxable, kind, place)
    tenant_id, account_id = uuid.uuid4(), uuid.uuid4()
    db.add(
        BillingProfile(
            tenant_id=tenant_id,
            legal_name="Sharma Furniture",
            gstin=gstin,
            address_line1="Paud Road",
            city="Pune",
            state_code=buyer_state if country == "IN" else None,
            country=country,
        )
    )
    order = db.add(
        PaymentOrder(
            tenant_id=tenant_id,
            account_id=account_id,
            purpose="top_up",
            currency="INR",
            taxable_minor=split.taxable_minor,
            cgst_minor=split.cgst_minor,
            sgst_minor=split.sgst_minor,
            igst_minor=split.igst_minor,
            gross_minor=split.total_minor,
            tax_kind=kind,
            place_of_supply=place,
            description=description,
            sac_code=sac,
            provider="razorpay",
            idempotency_key=uuid.uuid4().hex,
            created_by="asha@shree.example",
        )
    )
    payment = db.add(
        Payment(
            order_id=order.id,
            tenant_id=tenant_id,
            account_id=account_id,
            provider="razorpay",
            provider_payment_id=f"pay_{uuid.uuid4().hex[:14]}",
            status="captured",
            currency="INR",
            gross_minor=split.total_minor,
            taxable_minor=split.taxable_minor,
            cgst_minor=split.cgst_minor,
            sgst_minor=split.sgst_minor,
            igst_minor=split.igst_minor,
            source="webhook",
        )
    )
    return payment, order


async def test_maharashtra_business_with_gstin_gets_cgst_and_sgst() -> None:
    db = FakeDB()
    seed(db)
    payment, order = paid(db)
    doc = await issue_invoice(db.session(), payment, order, now=NOW)
    assert (doc.doc_type, doc.number, doc.fy, doc.kind, doc.place_of_supply) == (
        "invoice",
        "INV/26-27/00001",
        "26-27",
        "intra",
        "27",
    )
    assert (doc.taxable_minor, doc.cgst_minor, doc.sgst_minor, doc.igst_minor) == (
        100_000,
        9_000,
        9_000,
        0,
    )
    assert doc.total_minor == 118_000 == payment.gross_minor
    assert doc.seller["gstin"] == "27AAPFU0939F1ZV" and doc.seller["sac"] == "998315"
    assert doc.buyer["gstin"] == "27AAACB2230M1ZP" and doc.buyer["state_name"] == "Maharashtra"
    assert doc.lines == [
        {
            "description": "Prepaid balance top-up",
            "sac": "998315",
            "qty": 1,
            "unit": "NOS",
            "taxable_minor": 100_000,
        }
    ]
    assert doc.reverse_charge is False and doc.payment_id == payment.id
    assert doc.refund_id is None and doc.original_id is None
    [audit] = [a for a in db.all(AuditLog) if a.action == "billing.invoice_issued"]
    assert audit.details["number"] == "INV/26-27/00001" and audit.details["total_minor"] == 118_000


async def test_karnataka_consumer_gets_igst() -> None:
    db = FakeDB()
    seed(db)
    payment, order = paid(db, kind="inter", place="29", buyer_state="29", gstin=None)
    doc = await issue_invoice(db.session(), payment, order, now=NOW)
    assert (doc.kind, doc.cgst_minor, doc.sgst_minor, doc.igst_minor) == ("inter", 0, 0, 18_000)
    assert doc.buyer["state_code"] == "29" and doc.buyer["state_name"] == "Karnataka"
    assert doc.buyer["gstin"] is None


async def test_seller_without_gstin_issues_a_bill_of_supply() -> None:
    db = FakeDB()
    seed(db, {"company": {"legal_name": "Asha Tutors", "state_code": "27"}})
    payment, order = paid(db, kind="no_gst")
    doc = await issue_invoice(db.session(), payment, order, now=NOW)
    assert doc.doc_type == "bill_of_supply"
    assert (doc.cgst_minor, doc.sgst_minor, doc.igst_minor) == (0, 0, 0)
    assert doc.total_minor == 100_000 and doc.seller["gstin"] is None


async def test_export_under_lut_is_a_tax_invoice_with_zero_tax() -> None:
    db = FakeDB()
    seed(
        db,
        {
            **SELLER,
            "invoicing": {**SELLER["invoicing"], "lut_number": "AD270326000123X"},
        },
    )
    payment, order = paid(db, kind="export_lut", place="96", country="AE", gstin=None)
    doc = await issue_invoice(db.session(), payment, order, now=NOW)
    assert (doc.doc_type, doc.kind, doc.total_minor) == ("invoice", "export_lut", 100_000)
    assert doc.seller["lut_number"] == "AD270326000123X"
    assert doc.buyer["country"] == "AE"


async def test_export_without_lut_pays_igst() -> None:
    db = FakeDB()
    seed(db)
    payment, order = paid(db, kind="export_igst", place="96", country="AE", gstin=None)
    doc = await issue_invoice(db.session(), payment, order, now=NOW)
    assert (doc.kind, doc.igst_minor, doc.total_minor) == ("export_igst", 18_000, 118_000)


async def test_credit_pack_is_invoiced_at_the_pack_price_and_names_the_pack() -> None:
    db = FakeDB()
    seed(db)
    payment, order = paid(db, taxable=90_000, description="Credit pack ₹1,000 (+10 % bonus)")
    doc = await issue_invoice(db.session(), payment, order, now=NOW)
    assert doc.taxable_minor == 90_000
    assert doc.lines[0]["description"] == "Credit pack ₹1,000 (+10 % bonus)"


async def test_service_add_on_uses_its_own_sac() -> None:
    db = FakeDB()
    seed(db)
    payment, order = paid(db, description="Add-on: Installation help", sac="998313")
    doc = await issue_invoice(db.session(), payment, order, now=NOW)
    assert doc.lines[0]["sac"] == "998313"


async def test_the_same_payment_twice_gives_one_invoice() -> None:
    db = FakeDB()
    seed(db)
    payment, order = paid(db)
    session = db.session()
    first = await issue_invoice(session, payment, order, now=NOW)
    second = await issue_invoice(session, payment, order, now=NOW)
    assert first is second
    assert len(db.all(TaxDocument)) == 1
    assert await invoice_of_payment(session, payment.id) is first


async def test_numbers_follow_each_other_and_the_prefix_comes_from_the_setting() -> None:
    db = FakeDB()
    seed(db, {**SELLER, "invoicing": {**SELLER["invoicing"], "invoice_prefix": "CB"}})
    numbers = []
    for _ in range(3):
        payment, order = paid(db)
        numbers.append((await issue_invoice(db.session(), payment, order, now=NOW)).number)
    assert numbers == ["CB/26-27/00001", "CB/26-27/00002", "CB/26-27/00003"]


async def test_issue_date_in_ist_picks_the_financial_year() -> None:
    db = FakeDB()
    seed(db)
    payment, order = paid(db)
    late = datetime(2027, 3, 31, 18, 30, tzinfo=UTC)  # 00:00 IST on 1 April 2027
    doc = await issue_invoice(db.session(), payment, order, now=late)
    assert doc.fy == "27-28" and doc.number == "INV/27-28/00001"


async def test_snapshot_does_not_follow_later_edits_to_the_tax_details() -> None:
    db = FakeDB()
    seed(db)
    payment, order = paid(db)
    doc = await issue_invoice(db.session(), payment, order, now=NOW)
    profile = next(p for p in db.all(BillingProfile) if p.tenant_id == payment.tenant_id)
    profile.legal_name = "Renamed Pvt Ltd"
    profile.state_code = "29"
    assert doc.buyer["legal_name"] == "Sharma Furniture" and doc.buyer["state_code"] == "27"


async def test_deleted_workspace_still_gets_a_stand_in_buyer() -> None:
    db = FakeDB()
    seed(db)
    payment, order = paid(db)
    db.rows[BillingProfile].clear()
    doc = await issue_invoice(db.session(), payment, order, now=NOW)
    assert doc.buyer["legal_name"] == "a deleted workspace"
    assert doc.buyer["state_code"] == "27"
    assert buyer_snapshot(None, order, "X")["country"] == "IN"


@pytest.mark.parametrize(
    ("invoice_day", "deadline"),
    [
        (date(2026, 10, 8), date(2027, 11, 30)),
        (date(2027, 3, 31), date(2027, 11, 30)),
        (date(2027, 4, 1), date(2028, 11, 30)),
    ],
)
def test_credit_note_deadline_is_30_november_after_the_financial_year(
    invoice_day: date, deadline: date
) -> None:
    assert credit_note_deadline(invoice_day) == deadline


async def test_invoice_is_dated_when_processed_not_when_the_provider_took_the_payment() -> None:
    from callobuzz_rag.billing.payments import documents as seam

    db = FakeDB()
    seed(db)
    payment, order = paid(db)
    payment.captured_at = datetime(2027, 3, 31, 18, 20, tzinfo=UTC)  # 23:50 IST on 31 March
    applied = datetime(2027, 4, 1, 4, 0, tzinfo=UTC)  # a late webhook, 1 April 09:30 IST
    await seam.on_payment_captured(db.session(), payment, order, now=applied)
    [doc] = db.all(TaxDocument)
    assert doc.number == "INV/27-28/00001" and doc.fy == "27-28"
    assert doc.issued_at == applied
