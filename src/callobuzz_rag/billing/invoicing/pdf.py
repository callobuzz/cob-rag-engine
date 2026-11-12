"""The PDF of a tax invoice, bill of supply or credit note (reportlab, A4).

`render(document, reference=...)` returns the bytes. The same row always renders to the same bytes
(`invariant=1`: no creation date, a fixed document id), so the SHA-256 stored on the row can be
reproduced. The bundled Noto Sans (SIL Open Font License, `fonts/OFL.txt`) carries the rupee sign
and Latin letters; names and addresses are Latin script by validation (`api/schemas/payments.py`).
Every Rule 46 field is printed: seller name, address and GSTIN; number and date; buyer name,
address and GSTIN; place of supply; SAC, description, quantity, taxable value, rate and amount of
each tax; total in figures and words; whether tax is payable on reverse charge; the signature line.
"""

import io
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Final
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from callobuzz_rag.billing.invoicing.documents import local_date
from callobuzz_rag.billing.invoicing.words import amount_in_words
from callobuzz_rag.billing.money import Money
from callobuzz_rag.core.company import OTHER_COUNTRY_STATE, STATES

FONT_DIR: Final = Path(__file__).parent / "fonts"
REGULAR: Final = "NotoSans"
BOLD: Final = "NotoSans-Bold"

TITLES: Final = {
    "invoice": "Tax Invoice",
    "bill_of_supply": "Bill of Supply",
    "credit_note": "Credit Note",
}
LUT_WORDING: Final = (
    "SUPPLY MEANT FOR EXPORT UNDER LETTER OF UNDERTAKING WITHOUT PAYMENT OF INTEGRATED TAX"
)
NOT_REGISTERED: Final = "The seller is not registered under GST. No GST has been charged."
INK: Final = colors.HexColor("#1a1a1a")
MUTED: Final = colors.HexColor("#5b5b5b")
RULE: Final = colors.HexColor("#c9c9c9")
TINT: Final = colors.HexColor("#f2f2f2")


@lru_cache(maxsize=1)
def _fonts() -> None:
    pdfmetrics.registerFont(TTFont(REGULAR, str(FONT_DIR / "NotoSans-Regular.ttf")))
    pdfmetrics.registerFont(TTFont(BOLD, str(FONT_DIR / "NotoSans-Bold.ttf")))
    pdfmetrics.registerFontFamily(
        REGULAR, normal=REGULAR, bold=BOLD, italic=REGULAR, boldItalic=BOLD
    )


def _style(name: str, size: float = 9, **kw: Any) -> ParagraphStyle:
    return ParagraphStyle(
        name, fontName=kw.pop("fontName", REGULAR), fontSize=size, leading=size * 1.35,
        textColor=kw.pop("textColor", INK), **kw,
    )  # fmt: skip


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def _money(minor: int, currency: str) -> str:
    return Money(minor, currency).format()  # type: ignore[arg-type]


def _date(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def _place(code: str) -> str:
    if code == OTHER_COUNTRY_STATE:
        return f"Outside India ({code})"
    name = STATES.get(code)
    return f"{name} ({code})" if name else code


def _address(party: dict[str, Any]) -> list[str]:
    city_line = " ".join(x for x in (party.get("city"), party.get("pincode")) if x)
    lines = [party.get("address_line1"), party.get("address_line2"), city_line]
    state = party.get("state_name")
    if state and party.get("country", "IN") == "IN":
        lines.append(state)
    elif party.get("country") not in (None, "IN"):
        lines.append(f"Country: {party['country']}")
    return [str(x) for x in lines if x]


def _party(title: str, party: dict[str, Any], *, seller: bool) -> list[Paragraph]:
    name = party.get("legal_name") or ""
    trade = party.get("trade_name")
    rows = [_p(title, _style("h", 8, textColor=MUTED)), _p(name, _style("n", 11, fontName=BOLD))]
    small = _style("s", 9)
    if trade and trade != name:
        rows.append(_p(f"Trading as {trade}", small))
    rows.extend(_p(line, small) for line in _address(party))
    if party.get("gstin"):
        rows.append(_p(f"GSTIN: {party['gstin']}", small))
    elif not seller:
        rows.append(_p("GSTIN: not registered", small))
    contact = party.get("email") or party.get("email_for_invoices")
    if contact:
        rows.append(_p(str(contact), small))
    return rows


def _tax_rows(doc: Any, currency: str) -> list[tuple[str, str]]:
    kind = doc.kind
    if doc.doc_type == "bill_of_supply" or kind == "no_gst":
        return []
    if kind == "intra":
        return [
            ("CGST @ 9%", _money(doc.cgst_minor, currency)),
            ("SGST @ 9%", _money(doc.sgst_minor, currency)),
        ]
    if kind == "export_lut":
        return [("IGST @ 0% (export under LUT)", _money(0, currency))]
    return [("IGST @ 18%", _money(doc.igst_minor, currency))]


def render(document: Any, *, reference: str | None = None, original: Any = None) -> bytes:
    """The document as PDF bytes. `reference` is the payment (or refund) id shown as the
    payment reference; a credit note names its `original` invoice (number and date)."""
    _fonts()
    doc_type: str = document.doc_type
    currency: str = document.currency
    seller: dict[str, Any] = document.seller
    buyer: dict[str, Any] = document.buyer
    title = TITLES[doc_type]
    issued = local_date(document.issued_at)

    buffer = io.BytesIO()
    page = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm, invariant=1,
        title=f"{title} {document.number}", author=str(seller.get("legal_name") or ""),
        subject=title,
    )  # fmt: skip
    width = A4[0] - 30 * mm
    small = _style("small", 9)
    right = _style("right", 9, alignment=TA_RIGHT)
    right_bold = _style("rightb", 10, fontName=BOLD, alignment=TA_RIGHT)
    story: list[Any] = [_p(title, _style("title", 20, fontName=BOLD)), Spacer(1, 3 * mm)]

    meta = [
        _p(f"{title} No.: {document.number}", _style("m1", 10, fontName=BOLD, alignment=TA_RIGHT)),
        _p(f"Date of issue: {_date(issued)}", right),
        _p(f"Place of supply: {_place(document.place_of_supply)}", right),
        _p("Reverse charge: " + ("Yes" if document.reverse_charge else "No"), right),
    ]
    if doc_type == "credit_note" and original is not None:
        meta.insert(
            1,
            _p(
                f"Against invoice: {original.number} dated {_date(local_date(original.issued_at))}",
                right,
            ),
        )
    head = Table(
        [[_party("From", seller, seller=True), meta]], colWidths=[width * 0.55, width * 0.45]
    )
    head.setStyle(
        TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)])
    )
    story += [head, Spacer(1, 4 * mm)]

    bill = Table([[_party("Billed to", buyer, seller=False)]], colWidths=[width])
    bill.setStyle(
        TableStyle(
            [("BOX", (0, 0), (-1, -1), 0.5, RULE), ("BACKGROUND", (0, 0), (-1, -1), TINT),
             ("LEFTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 5),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]
        )
    )  # fmt: skip
    story += [bill, Spacer(1, 5 * mm)]

    header = [_p(h, _style("th", 8, fontName=BOLD, textColor=MUTED)) for h in
              ("#", "Description", "SAC", "Qty", "Unit", "Taxable value")]  # fmt: skip
    rows: list[list[Any]] = [header]
    for index, line in enumerate(document.lines, start=1):
        rows.append(
            [
                _p(str(index), small),
                _p(str(line["description"]), small),
                _p(f"SAC {line['sac']}", small),
                _p(str(line["qty"]), small),
                _p(str(line["unit"]), small),
                _p(_money(int(line["taxable_minor"]), currency), right),
            ]
        )
    cols = [
        8 * mm,
        width - 8 * mm - 24 * mm - 12 * mm - 14 * mm - 32 * mm,
        24 * mm,
        12 * mm,
        14 * mm,
        32 * mm,
    ]
    lines_table = Table(rows, colWidths=cols, repeatRows=1)
    lines_table.setStyle(
        TableStyle(
            [("LINEBELOW", (0, 0), (-1, 0), 0.6, INK), ("LINEBELOW", (0, 1), (-1, -1), 0.3, RULE),
             ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 3)]
        )
    )  # fmt: skip
    story += [lines_table, Spacer(1, 3 * mm)]

    totals: list[list[Any]] = [
        [_p("Taxable value", right), _p(_money(document.taxable_minor, currency), right)]
    ]
    totals += [
        [_p(label, right), _p(amount, right)] for label, amount in _tax_rows(document, currency)
    ]
    totals.append([_p("Total", right_bold), _p(_money(document.total_minor, currency), right_bold)])
    totals_table = Table(totals, colWidths=[width - 40 * mm, 40 * mm])
    totals_table.setStyle(
        TableStyle([("LINEABOVE", (0, -1), (-1, -1), 0.6, INK), ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2)])
    )  # fmt: skip
    story += [totals_table, Spacer(1, 3 * mm)]

    story.append(_p(f"Amount in words: {amount_in_words(document.total_minor)}", small))
    if doc_type == "credit_note":
        story.append(_p("This credit note reduces the tax invoice it refers to.", small))
    elif reference:
        story.append(_p("Status: Paid", small))
    if reference:
        label = "Refund reference" if doc_type == "credit_note" else "Payment reference"
        story.append(_p(f"{label}: {reference}", small))
    story.append(Spacer(1, 3 * mm))
    if document.kind == "export_lut":
        lut = f"LUT No.: {seller.get('lut_number') or ''}"
        if seller.get("lut_valid_until"):
            lut += f" (valid until {seller['lut_valid_until']})"
        story += [_p(LUT_WORDING, _style("lut", 9, fontName=BOLD)), _p(lut, small)]
    elif doc_type == "bill_of_supply":
        story.append(_p(NOT_REGISTERED, small))
    story.append(Spacer(1, 10 * mm))
    muted = _style("muted", 8.5, textColor=MUTED)
    story.append(_p(str(seller.get("legal_name") or ""), _style("sig", 9, fontName=BOLD)))
    if seller.get("signature_line"):
        story.append(_p(str(seller["signature_line"]), muted))
    if seller.get("footer_text"):
        story += [Spacer(1, 3 * mm), _p(str(seller["footer_text"]), muted)]
    page.build(story)
    return buffer.getvalue()
