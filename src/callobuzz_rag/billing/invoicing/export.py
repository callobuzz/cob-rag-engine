"""The CSV an accountant files returns from: one row per tax document in a date range.

Columns: type, number, date, buyer_name, buyer_gstin, b2b_or_b2c, place_of_supply, state_name,
sac, taxable, cgst, sgst, igst, total, original_number, payment_reference. Amounts are decimal
strings made from integer minor units (`1178.82`, never a float); a credit note's are negative.
A cell that would start a spreadsheet formula is prefixed with a quote, so opening the file
never runs anything.
"""

import csv
import io
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any, Final

from callobuzz_rag.billing.invoicing.documents import local_date
from callobuzz_rag.billing.invoicing.numbering import IST
from callobuzz_rag.billing.money import Money
from callobuzz_rag.db.models.tax_document import TaxDocument

MAX_DAYS: Final = 366
HEADER: Final = (
    "type",
    "number",
    "date",
    "buyer_name",
    "buyer_gstin",
    "b2b_or_b2c",
    "place_of_supply",
    "state_name",
    "sac",
    "taxable",
    "cgst",
    "sgst",
    "igst",
    "total",
    "original_number",
    "payment_reference",
)
_FORMULA_START: Final = ("=", "+", "-", "@", "\t", "\r")


def day_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """UTC bounds of the Indian days `start` to `end`, both included."""
    return (
        datetime.combine(start, time.min, tzinfo=IST),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=IST),
    )


def _safe(value: object) -> str:
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(_FORMULA_START) else text


def _amount(minor: int, currency: str) -> str:
    return Money(minor, currency).amount_str()  # type: ignore[arg-type]


def _supply_kind(doc: TaxDocument, buyer: Mapping[str, Any]) -> str:
    if (doc.kind or "").startswith("export"):
        return "EXPORT"
    return "B2B" if buyer.get("gstin") else "B2C"


def render_csv(
    documents: Sequence[TaxDocument],
    *,
    originals: Mapping[Any, str],
    references: Mapping[Any, str],
) -> str:
    """`originals`: credit note's original id -> number; `references`: payment id -> provider id."""
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(HEADER)
    for doc in documents:
        buyer = doc.buyer or {}
        line = doc.lines[0] if doc.lines else {}
        writer.writerow(
            [
                doc.doc_type,
                doc.number,
                local_date(doc.issued_at).isoformat(),
                _safe(buyer.get("legal_name")),
                _safe(buyer.get("gstin")),
                _supply_kind(doc, buyer),
                doc.place_of_supply,
                _safe(buyer.get("state_name")),
                _safe(line.get("sac")),
                _amount(doc.taxable_minor, doc.currency),
                _amount(doc.cgst_minor, doc.currency),
                _amount(doc.sgst_minor, doc.currency),
                _amount(doc.igst_minor, doc.currency),
                _amount(doc.total_minor, doc.currency),
                originals.get(doc.original_id, "") if doc.original_id else "",
                references.get(doc.payment_id, ""),
            ]
        )
    return out.getvalue()
