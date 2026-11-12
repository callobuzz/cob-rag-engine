"""Bodies for tax invoices (P2). Money is an integer in minor units next to a `currency`."""

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

DocType = Literal["invoice", "bill_of_supply", "credit_note"]


class TaxDocumentView(BaseModel):
    """One tax invoice, bill of supply or credit note as the business sees it."""

    id: uuid.UUID
    doc_type: DocType
    number: str
    issued_on: date = Field(..., description="The date of issue, Indian time.")
    currency: str
    taxable_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int
    total_minor: int = Field(..., description="Negative for a credit note.")
    original_number: str | None = Field(None, description="The invoice a credit note reduces.")
    pdf_ready: bool = Field(..., description="False for a moment after the payment.")


class TaxDocumentList(BaseModel):
    items: list[TaxDocumentView]
    total: int


class AdminTaxDocumentRow(TaxDocumentView):
    tenant_id: uuid.UUID
    buyer_name: str
    buyer_gstin: str | None
    fy: str
    kind: str
    place_of_supply: str
    payment_id: uuid.UUID


class TaxDocumentTotals(BaseModel):
    """The sums over every document the filter matches (credit notes subtract)."""

    taxable_minor: int
    cgst_minor: int
    sgst_minor: int
    igst_minor: int
    total_minor: int


class AdminTaxDocumentList(BaseModel):
    items: list[AdminTaxDocumentRow]
    total: int
    totals: TaxDocumentTotals


class ResendOut(BaseModel):
    queued: Literal[True] = True
