"""P2 T5: the PDF of a tax document, read back as text with pypdfium2."""

import hashlib
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import pypdfium2 as pdfium

from callobuzz_rag.billing.invoicing.pdf import render
from callobuzz_rag.db.models.tax_document import TaxDocument

ISSUED = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)
SELLER: dict[str, Any] = {
    "legal_name": "Callobuzz Technologies",
    "trade_name": "Callobuzz",
    "address_line1": "12 Baner Road",
    "address_line2": None,
    "city": "Pune",
    "state_code": "27",
    "state_name": "Maharashtra",
    "pincode": "411045",
    "country": "IN",
    "email": "billing@callobuzz.example",
    "phone": None,
    "gstin": "27AAPFU0939F1ZV",
    "sac": "998315",
    "lut_number": None,
    "lut_valid_until": None,
    "signature_line": "This is a computer-generated invoice.",
    "footer_text": None,
}
BUYER: dict[str, Any] = {
    "legal_name": "Sharma Furniture",
    "gstin": "27AAACB2230M1ZP",
    "address_line1": "Paud Road, Kothrud",
    "address_line2": None,
    "city": "Pune",
    "state_code": "27",
    "state_name": "Maharashtra",
    "pincode": "411038",
    "country": "IN",
    "email_for_invoices": "accounts@sharma.example",
}


def doc(**kw: Any) -> TaxDocument:
    base: dict[str, Any] = {
        "id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
        "doc_type": "invoice",
        "number": "INV/26-27/00001",
        "fy": "26-27",
        "issued_at": ISSUED,
        "kind": "intra",
        "place_of_supply": "27",
        "currency": "INR",
        "taxable_minor": 99_900,
        "cgst_minor": 8_991,
        "sgst_minor": 8_991,
        "igst_minor": 0,
        "total_minor": 117_882,
        "seller": SELLER,
        "buyer": BUYER,
        "lines": [
            {
                "description": "Prepaid balance top-up",
                "sac": "998315",
                "qty": 1,
                "unit": "NOS",
                "taxable_minor": 99_900,
            }
        ],
        "reverse_charge": False,
    }
    return TaxDocument(**{**base, **kw})


def text_of(pdf: bytes) -> str:
    page_text = []
    document = pdfium.PdfDocument(pdf)
    for index in range(len(document)):
        page_text.append(document[index].get_textpage().get_text_range())
    return re.sub(r"\s+", " ", " ".join(page_text))


def test_tax_invoice_carries_every_rule_46_field() -> None:
    text = text_of(render(doc(), reference="pay_Nabc123def456"))
    for expected in (
        "Tax Invoice",
        "INV/26-27/00001",
        "Date of issue: 08-10-2026",
        "Callobuzz Technologies",
        "GSTIN: 27AAPFU0939F1ZV",
        "Sharma Furniture",
        "GSTIN: 27AAACB2230M1ZP",
        "Place of supply: Maharashtra (27)",
        "SAC 998315",
        "Prepaid balance top-up",
        "CGST @ 9%",
        "SGST @ 9%",
        "₹1,178.82",
        "One Thousand One Hundred Seventy-Eight Rupees and Eighty-Two Paise Only",
        "Reverse charge: No",
        "Paid",
        "Payment reference: pay_Nabc123def456",
        "This is a computer-generated invoice.",
    ):
        assert expected in text, expected


def test_interstate_invoice_shows_igst_not_cgst() -> None:
    text = text_of(
        render(
            doc(
                kind="inter",
                place_of_supply="29",
                cgst_minor=0,
                sgst_minor=0,
                igst_minor=17_982,
                buyer={**BUYER, "gstin": None, "state_code": "29", "state_name": "Karnataka"},
            )
        )
    )
    assert "IGST @ 18%" in text and "CGST" not in text
    assert "Place of supply: Karnataka (29)" in text and "GSTIN: not registered" in text


def test_export_under_lut_carries_the_standard_wording_and_the_lut_number() -> None:
    text = text_of(
        render(
            doc(
                kind="export_lut",
                place_of_supply="96",
                cgst_minor=0,
                sgst_minor=0,
                total_minor=99_900,
                seller={**SELLER, "lut_number": "AD270326000123X"},
                buyer={**BUYER, "gstin": None, "country": "AE", "state_name": None},
            )
        )
    )
    assert (
        "SUPPLY MEANT FOR EXPORT UNDER LETTER OF UNDERTAKING WITHOUT PAYMENT OF INTEGRATED TAX"
        in text
    )
    assert "AD270326000123X" in text and "Country: AE" in text
    assert "Place of supply: Outside India (96)" in text


def test_bill_of_supply_has_no_tax_lines() -> None:
    text = text_of(
        render(
            doc(
                doc_type="bill_of_supply",
                kind="no_gst",
                cgst_minor=0,
                sgst_minor=0,
                total_minor=99_900,
                seller={**SELLER, "gstin": None},
            )
        )
    )
    assert "Bill of Supply" in text and "CGST" not in text and "IGST" not in text
    assert "not registered under GST" in text


def test_credit_note_names_the_original_and_is_negative() -> None:
    original = doc()
    note = doc(
        id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        doc_type="credit_note",
        number="CN/26-27/00001",
        taxable_minor=-50_000,
        cgst_minor=-4_500,
        sgst_minor=-4_500,
        total_minor=-59_000,
    )
    text = text_of(render(note, reference="rfnd_Nxyz", original=original))
    assert "Credit Note" in text and "CN/26-27/00001" in text
    assert "Against invoice: INV/26-27/00001 dated 08-10-2026" in text
    assert "−₹590.00" in text  # a real minus sign
    assert "Refund reference: rfnd_Nxyz" in text


def test_output_is_deterministic_so_the_stored_hash_is_reproducible() -> None:
    first = render(doc(), reference="pay_1")
    second = render(doc(), reference="pay_1")
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    assert first.startswith(b"%PDF")


def test_user_text_cannot_inject_markup() -> None:
    text = text_of(render(doc(buyer={**BUYER, "legal_name": "A <b>& B</b> Traders"})))
    assert "A <b>& B</b> Traders" in text
