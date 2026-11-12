"""P2 T6: the owner's invoice list and downloads, the operator's list, CSV and resend.

Through the real app on the in-memory database; the PDF bytes come from a stand-in storage.
"""

import csv
import io
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
from callobuzz_rag.api.routes import invoices
from callobuzz_rag.db.models.payment import BillingProfile, Payment
from callobuzz_rag.db.models.tax_document import TaxDocument
from tests.unit.accounts_fakes import FakeRedis
from tests.unit.test_auth_routes import ADMIN, World, auth_app, login, req

OWNER = "/api/v1/billing/invoices"
ADMIN_BASE = "/api/v1/admin/billing/invoices"
PDF = b"%PDF-1.4 fake"


class Storage:
    def __init__(self) -> None:
        self.objects = {("rag-documents", "k1"): PDF}

    async def download(self, bucket: str, key: str) -> bytes:
        return self.objects[(bucket, key)]


@pytest.fixture
def world() -> World:
    invalidate_engine_settings_cache()
    w = World()
    w.member(w.user("asha@shree.example"), w.shree, "owner")
    w.member(w.user("ravi@shree.example"), w.shree, "member")
    w.member(w.user("meena@pune.example"), w.pune, "owner")
    return w


@contextmanager
def app_for(world: World) -> Iterator[Any]:
    with auth_app(world) as app, patch.object(invoices, "object_storage", lambda s: Storage()):
        app.state.invoices_redis = FakeRedis()
        yield app


def add_doc(
    world: World,
    tenant: Any,
    number: str,
    *,
    when: datetime,
    doc_type: str = "invoice",
    taxable: int = 100_000,
    pdf: bool = True,
    original: TaxDocument | None = None,
    buyer: dict[str, Any] | None = None,
    fy: str = "26-27",
    kind: str = "intra",
) -> TaxDocument:
    sign = -1 if doc_type == "credit_note" else 1
    tax = taxable * 9 // 100 * sign
    taxable *= sign
    payment_id = original.payment_id if original else uuid.uuid4()
    return world.db.add(
        TaxDocument(
            id=uuid.uuid4(),
            doc_type=doc_type,
            number=number,
            fy=fy,
            issued_at=when,
            tenant_id=tenant.id,
            account_id=uuid.uuid4(),
            payment_id=payment_id,
            refund_id=uuid.uuid4() if doc_type == "credit_note" else None,
            original_id=original.id if original else None,
            kind=kind,
            place_of_supply="27",
            currency="INR",
            taxable_minor=taxable,
            cgst_minor=tax,
            sgst_minor=tax,
            igst_minor=0,
            total_minor=taxable + 2 * tax,
            seller={},
            buyer=buyer
            or {
                "legal_name": "Sharma Furniture",
                "gstin": "27AAACB2230M1ZP",
                "state_name": "Maharashtra",
            },
            lines=[
                {
                    "description": "Prepaid balance top-up",
                    "sac": "998315",
                    "qty": 1,
                    "unit": "NOS",
                    "taxable_minor": taxable,
                }
            ],
            pdf_key="k1" if pdf else None,
            pdf_sha256="ab" if pdf else None,
        )
    )


def t(day: int, hour: int = 6) -> datetime:
    return datetime(2026, 10, day, hour, 0, tzinfo=UTC)


async def owner(app: Any, email: str = "asha@shree.example") -> str:
    return str((await login(app, email)).json()["access_token"])


async def test_owner_lists_own_documents_newest_first(world: World) -> None:
    first = add_doc(world, world.shree, "INV/26-27/00001", when=t(1))
    add_doc(world, world.shree, "INV/26-27/00002", when=t(5))
    add_doc(world, world.shree, "CN/26-27/00001", when=t(6), doc_type="credit_note", original=first)
    add_doc(world, world.pune, "INV/26-27/00003", when=t(7))
    with app_for(world) as app:
        res = await req(app, "GET", OWNER, token=await owner(app))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 3
    assert [i["number"] for i in body["items"]] == [
        "CN/26-27/00001",
        "INV/26-27/00002",
        "INV/26-27/00001",
    ]
    note = body["items"][0]
    assert note["doc_type"] == "credit_note" and note["original_number"] == "INV/26-27/00001"
    assert note["total_minor"] == -118_000 and note["issued_on"] == "2026-10-06"
    assert all(i["pdf_ready"] for i in body["items"])
    assert "tenant_id" not in note and "seller" not in note


async def test_a_member_is_refused(world: World) -> None:
    with app_for(world) as app:
        res = await req(app, "GET", OWNER, token=await owner(app, "ravi@shree.example"))
    assert res.status_code == 403 and res.json()["code"] == "owner_required"


async def test_another_workspaces_document_is_a_404(world: World) -> None:
    other = add_doc(world, world.pune, "INV/26-27/00009", when=t(2))
    with app_for(world) as app:
        res = await req(app, "GET", f"{OWNER}/{other.id}/pdf", token=await owner(app))
        missing = await req(app, "GET", f"{OWNER}/{uuid.uuid4()}/pdf", token=await owner(app))
    assert res.status_code == 404 and res.json()["code"] == "tax_document_not_found"
    assert missing.status_code == 404


async def test_pdf_download_headers(world: World) -> None:
    doc = add_doc(world, world.shree, "INV/26-27/00001", when=t(2))
    with app_for(world) as app:
        res = await req(app, "GET", f"{OWNER}/{doc.id}/pdf", token=await owner(app))
    assert res.status_code == 200 and res.content == PDF
    assert res.headers["content-type"] == "application/pdf"
    assert res.headers["content-disposition"] == 'attachment; filename="INV-26-27-00001.pdf"'
    assert res.headers["cache-control"] == "private, no-store"


async def test_pdf_not_written_yet_is_409_with_retry_after(world: World) -> None:
    doc = add_doc(world, world.shree, "INV/26-27/00001", when=t(2), pdf=False)
    with app_for(world) as app:
        res = await req(app, "GET", f"{OWNER}/{doc.id}/pdf", token=await owner(app))
    assert res.status_code == 409 and res.json()["code"] == "tax_document_pdf_pending"
    assert res.headers["retry-after"] == "5"


async def test_a_knowledge_base_key_cannot_read_invoices(world: World) -> None:
    with app_for(world) as app:
        res = await req(app, "GET", OWNER, key="ck_not_a_real_key")
    assert res.status_code in (401, 403)


async def test_operator_lists_every_workspace_with_filters_and_totals(world: World) -> None:
    deleted_tenant = type("T", (), {"id": uuid.uuid4()})()  # a workspace that no longer exists
    one = add_doc(world, world.shree, "INV/26-27/00001", when=t(1))
    add_doc(world, world.pune, "INV/26-27/00002", when=t(10), taxable=200_000)
    add_doc(world, deleted_tenant, "INV/25-26/00099", when=t(3), fy="25-26")
    add_doc(world, world.shree, "CN/26-27/00001", when=t(4), doc_type="credit_note", original=one,
            taxable=50_000)  # fmt: skip
    with app_for(world) as app:
        everything = (await req(app, "GET", ADMIN_BASE, key=ADMIN)).json()
        only_shree = (
            await req(app, "GET", ADMIN_BASE, key=ADMIN, params={"tenant_id": str(world.shree.id)})
        ).json()
        only_notes = (
            await req(app, "GET", ADMIN_BASE, key=ADMIN, params={"doc_type": "credit_note"})
        ).json()
        old_year = (await req(app, "GET", ADMIN_BASE, key=ADMIN, params={"fy": "25-26"})).json()
        ranged = (
            await req(
                app,
                "GET",
                ADMIN_BASE,
                key=ADMIN,
                params={"start": "2026-10-02", "end": "2026-10-09"},
            )
        ).json()
    assert everything["total"] == 4
    assert everything["items"][0]["buyer_name"] == "Sharma Furniture"
    assert only_shree["total"] == 2 and only_notes["total"] == 1 and old_year["total"] == 1
    assert [i["number"] for i in ranged["items"]] == ["CN/26-27/00001", "INV/25-26/00099"]
    assert ranged["total"] == 2
    # 118,000 + 236,000 + 118,000 - 59,000 (the credit note subtracts)
    assert only_shree["totals"] == {
        "taxable_minor": 50_000,
        "cgst_minor": 4_500,
        "sgst_minor": 4_500,
        "igst_minor": 0,
        "total_minor": 59_000,
    }
    assert everything["totals"]["total_minor"] == 118_000 + 236_000 + 118_000 - 59_000


async def test_admin_routes_refuse_owners(world: World) -> None:
    with app_for(world) as app:
        res = await req(app, "GET", ADMIN_BASE, token=await owner(app))
    assert res.status_code == 403


async def test_operator_downloads_a_deleted_workspaces_pdf(world: World) -> None:
    gone = type("T", (), {"id": uuid.uuid4()})()
    doc = add_doc(world, gone, "INV/25-26/00099", when=t(3), fy="25-26")
    with app_for(world) as app:
        res = await req(app, "GET", f"{ADMIN_BASE}/{doc.id}/pdf", key=ADMIN)
    assert res.status_code == 200 and res.content == PDF


async def test_csv_for_the_accountant(world: World) -> None:
    invoice = add_doc(world, world.shree, "INV/26-27/00001", when=t(1))
    world.db.add(
        Payment(
            id=invoice.payment_id,
            order_id=uuid.uuid4(),
            tenant_id=world.shree.id,
            account_id=invoice.account_id,
            provider="razorpay",
            provider_payment_id="pay_Nabc123",
            status="captured",
            currency="INR",
            gross_minor=118_000,
            taxable_minor=100_000,
            source="webhook",
        )
    )
    add_doc(world, world.pune, "INV/26-27/00002", when=t(2), taxable=99_900,
            buyer={"legal_name": "=HYPERLINK(\"x\")", "gstin": None, "state_name": "Maharashtra"})  # fmt: skip
    add_doc(world, world.shree, "CN/26-27/00001", when=t(3), doc_type="credit_note",
            original=invoice, taxable=50_000)  # fmt: skip
    with app_for(world) as app:
        res = await req(
            app, "GET", f"{ADMIN_BASE}/export", key=ADMIN,
            params={"start": "2026-10-01", "end": "2026-10-31"},
        )  # fmt: skip
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(res.text)))
    assert rows[0] == [
        "type", "number", "date", "buyer_name", "buyer_gstin", "b2b_or_b2c", "place_of_supply",
        "state_name", "sac", "taxable", "cgst", "sgst", "igst", "total", "original_number",
        "payment_reference",
    ]  # fmt: skip
    by_number = {r[1]: r for r in rows[1:]}
    inv = by_number["INV/26-27/00001"]
    assert inv[0] == "invoice" and inv[2] == "2026-10-01" and inv[5] == "B2B"
    assert inv[9:14] == ["1000.00", "90.00", "90.00", "0.00", "1180.00"]
    assert inv[14] == "" and inv[15] == "pay_Nabc123"
    note = by_number["CN/26-27/00001"]
    assert note[0] == "credit_note" and note[9] == "-500.00" and note[13] == "-590.00"
    assert note[14] == "INV/26-27/00001"
    assert by_number["INV/26-27/00002"][5] == "B2C"
    assert by_number["INV/26-27/00002"][3].startswith("'=")  # no spreadsheet formula


async def test_csv_range_rules(world: World) -> None:
    with app_for(world) as app:
        long = await req(app, "GET", f"{ADMIN_BASE}/export", key=ADMIN,
                         params={"start": "2026-01-01", "end": "2027-01-02"})  # fmt: skip
        ok = await req(app, "GET", f"{ADMIN_BASE}/export", key=ADMIN,
                       params={"start": "2026-04-01", "end": "2027-03-31"})  # fmt: skip
        backwards = await req(app, "GET", f"{ADMIN_BASE}/export", key=ADMIN,
                              params={"start": "2026-05-01", "end": "2026-04-01"})  # fmt: skip
    assert long.status_code == 422 and long.json()["code"] == "range_too_long"
    assert ok.status_code == 200
    assert backwards.status_code == 422 and backwards.json()["code"] == "invalid_range"


async def test_resend_queues_the_mail_and_is_limited_to_once_in_ten_minutes(world: World) -> None:
    doc = add_doc(world, world.shree, "INV/26-27/00001", when=t(2))
    world.db.add(
        BillingProfile(
            tenant_id=world.shree.id,
            legal_name="Sharma Furniture",
            address_line1="Paud Road",
            city="Pune",
            state_code="27",
            country="IN",
            email_for_invoices="accounts@sharma.example",
        )
    )
    with app_for(world) as app:
        first = await req(app, "POST", f"{ADMIN_BASE}/{doc.id}/resend", key=ADMIN)
        again = await req(app, "POST", f"{ADMIN_BASE}/{doc.id}/resend", key=ADMIN)
        missing = await req(app, "POST", f"{ADMIN_BASE}/{uuid.uuid4()}/resend", key=ADMIN)
    assert first.status_code == 202 and first.json() == {"queued": True}
    assert [(m[0], m[1]) for m in world.mail] == [("invoice_issued", "accounts@sharma.example")]
    assert again.status_code == 429 and again.json()["code"] == "invoice_resend_too_soon"
    assert again.headers["retry-after"] == "600"
    assert missing.status_code == 404


async def test_csv_marks_exports_apart_from_domestic_sales(world: World) -> None:
    add_doc(world, world.shree, "INV/26-27/00003", when=t(4), kind="export_lut",
            buyer={"legal_name": "Acme Ltd", "gstin": None, "state_name": ""})  # fmt: skip
    add_doc(world, world.pune, "INV/26-27/00004", when=t(5),
            buyer={"legal_name": "Walk-in", "gstin": None, "state_name": "Maharashtra"})  # fmt: skip
    with app_for(world) as app:
        res = await req(
            app, "GET", f"{ADMIN_BASE}/export", key=ADMIN,
            params={"start": "2026-10-01", "end": "2026-10-31"},
        )  # fmt: skip
    by_number = {r[1]: r for r in csv.reader(io.StringIO(res.text))}
    assert by_number["INV/26-27/00003"][5] == "EXPORT"
    assert by_number["INV/26-27/00004"][5] == "B2C"
