"""Tax invoices, bills of supply and credit notes (GST Rule 46, section 34).

`issue_invoice` runs inside `apply_captured`'s transaction, right after the ledger entry: the
number is taken there, so a rollback gives it back (no gaps) and the invoice always matches the
money. `issue_credit_note` runs when a refund is processed. Both only add a row; the wallet is
never touched (the balance got the taxable value when the payment was captured). The PDF is
written later, after the commit (`worker/payment_tasks.py`).

The seller and the buyer are copied into the row (`seller`, `buyer`), so a later change to the
company details or to the business's tax details never alters an issued document.
"""

import logging
import uuid
from datetime import UTC, date, datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.accounts.settings_store import EngineSettings, read_engine_settings_row
from callobuzz_rag.billing import ledger
from callobuzz_rag.billing.invoicing.numbering import IST, financial_year, next_number
from callobuzz_rag.core.company import STATES
from callobuzz_rag.db.models.payment import BillingProfile, Payment, PaymentOrder, PaymentRefund
from callobuzz_rag.db.models.tax_document import TaxDocument
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

UNIT: Final = "NOS"
DELETED_WORKSPACE: Final = "a deleted workspace"


def credit_note_deadline(invoice_date: date) -> date:
    """30 November after the end of the financial year of the invoice (section 34(2))."""
    fy_end_year = invoice_date.year + 1 if invoice_date.month >= 4 else invoice_date.year
    return date(fy_end_year, 11, 30)


def local_date(moment: datetime) -> date:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(IST).date()


def seller_snapshot(settings: EngineSettings) -> dict[str, Any]:
    """Who sells: company, GST number, the default SAC, the LUT and the signature line."""
    company = settings.company
    tax = settings.invoicing
    return {
        "legal_name": company.legal_name,
        "trade_name": company.trade_name,
        "address_line1": company.address_line1,
        "address_line2": company.address_line2,
        "city": company.city,
        "state_code": company.state_code,
        "state_name": STATES.get(company.state_code or ""),
        "pincode": company.pincode,
        "country": company.country,
        "email": company.email,
        "phone": company.phone,
        "gstin": tax.gstin,
        "sac": tax.sac_default,
        "lut_number": tax.lut_number,
        "lut_valid_until": tax.lut_valid_until.isoformat() if tax.lut_valid_until else None,
        "signature_line": tax.signature_line,
        "footer_text": tax.footer_text,
    }


def buyer_snapshot(
    profile: BillingProfile | None, order: PaymentOrder, tenant_name: str
) -> dict[str, Any]:
    """Who buys: the workspace's tax details (a stand-in from the order if they are gone)."""
    if profile is None:
        state = order.place_of_supply if order.place_of_supply in STATES else None
        return {
            "legal_name": tenant_name,
            "gstin": None,
            "address_line1": None,
            "address_line2": None,
            "city": None,
            "state_code": state,
            "state_name": STATES.get(state or ""),
            "pincode": None,
            "country": "IN",
            "email_for_invoices": None,
        }
    return {
        "legal_name": profile.legal_name,
        "gstin": profile.gstin,
        "address_line1": profile.address_line1,
        "address_line2": profile.address_line2,
        "city": profile.city,
        "state_code": profile.state_code,
        "state_name": STATES.get(profile.state_code or ""),
        "pincode": profile.pincode,
        "country": profile.country,
        "email_for_invoices": profile.email_for_invoices,
    }


async def invoice_of_payment(session: AsyncSession, payment_id: uuid.UUID) -> TaxDocument | None:
    """The invoice or bill of supply of a payment (never its credit notes)."""
    rows = (
        (await session.execute(select(TaxDocument).where(TaxDocument.payment_id == payment_id)))
        .scalars()
        .all()
    )
    return next((r for r in rows if r.doc_type != "credit_note"), None)


async def credit_note_of_refund(session: AsyncSession, refund_id: uuid.UUID) -> TaxDocument | None:
    return (
        await session.execute(select(TaxDocument).where(TaxDocument.refund_id == refund_id))
    ).scalar_one_or_none()


async def _tenant_name(session: AsyncSession, tenant_id: uuid.UUID) -> str:
    tenant = await session.get(Tenant, tenant_id)
    return tenant.name if tenant is not None else DELETED_WORKSPACE


async def issue_invoice(
    session: AsyncSession, payment: Payment, order: PaymentOrder, *, now: datetime
) -> TaxDocument:
    """The tax invoice (or bill of supply) of a captured payment. Idempotent per payment.

    A seller without a GSTIN issues a bill of supply with no tax lines; everyone else a tax
    invoice, with the LUT wording and zero tax for an export under LUT. Does not commit.
    """
    existing = await invoice_of_payment(session, payment.id)
    if existing is not None:
        return existing

    settings = (await read_engine_settings_row(session))[0]
    profile = (
        await session.execute(
            select(BillingProfile).where(BillingProfile.tenant_id == order.tenant_id)
        )
    ).scalar_one_or_none()
    tenant_name = await _tenant_name(session, order.tenant_id)

    doc_type = "bill_of_supply" if order.tax_kind == "no_gst" else "invoice"
    fy = financial_year(now)
    _, number = await next_number(
        session, series="invoice", prefix=settings.invoicing.invoice_prefix, fy=fy
    )
    taxable, cgst, sgst, igst = (
        payment.taxable_minor,
        payment.cgst_minor,
        payment.sgst_minor,
        payment.igst_minor,
    )
    document = TaxDocument(
        id=uuid.uuid4(),
        doc_type=doc_type,
        number=number,
        fy=fy,
        issued_at=now,
        tenant_id=payment.tenant_id,
        account_id=payment.account_id,
        payment_id=payment.id,
        kind=order.tax_kind,
        place_of_supply=order.place_of_supply,
        currency=payment.currency,
        taxable_minor=taxable,
        cgst_minor=cgst,
        sgst_minor=sgst,
        igst_minor=igst,
        total_minor=taxable + cgst + sgst + igst,
        seller=seller_snapshot(settings),
        buyer=buyer_snapshot(profile, order, tenant_name),
        lines=[
            {
                "description": order.description,
                "sac": order.sac_code,
                "qty": 1,
                "unit": UNIT,
                "taxable_minor": taxable,
            }
        ],
        reverse_charge=False,
    )
    session.add(document)
    await session.flush()
    await record_audit(
        session,
        None,
        "billing.invoice_issued",
        tenant_id=payment.tenant_id,
        tenant_label=tenant_name,
        target_type="tax_document",
        target_id=str(document.id),
        details={
            "document_id": str(document.id),
            "number": number,
            "total_minor": document.total_minor,
        },
        actor=ledger.SYSTEM_ACTOR,
    )
    return document


async def issue_credit_note(
    session: AsyncSession, refund: PaymentRefund, *, now: datetime
) -> TaxDocument | None:
    """The credit note of a processed refund, the negative of the refund's own split.

    None when the invoice's deadline (30 November after its financial year) has passed or the
    payment has no invoice; `refund.credit_note_skipped` then says why. Idempotent per refund.
    """
    existing = await credit_note_of_refund(session, refund.id)
    if existing is not None:
        return existing
    if refund.credit_note_skipped is not None:
        return None

    original = await invoice_of_payment(session, refund.payment_id)
    if original is None:
        refund.credit_note_skipped = "no_invoice"
        logger.warning("Refund %s has no invoice to credit", refund.id)
        return None
    if local_date(now) > credit_note_deadline(local_date(original.issued_at)):
        refund.credit_note_skipped = "deadline"
        return None

    settings = (await read_engine_settings_row(session))[0]
    fy = financial_year(now)
    _, number = await next_number(
        session, series="credit_note", prefix=settings.invoicing.credit_note_prefix, fy=fy
    )
    taxable, cgst, sgst, igst = (
        -refund.taxable_minor,
        -refund.cgst_minor,
        -refund.sgst_minor,
        -refund.igst_minor,
    )
    source_line = original.lines[0] if original.lines else {}
    document = TaxDocument(
        id=uuid.uuid4(),
        doc_type="credit_note",
        number=number,
        fy=fy,
        issued_at=now,
        tenant_id=refund.tenant_id,
        account_id=refund.account_id,
        payment_id=refund.payment_id,
        refund_id=refund.id,
        original_id=original.id,
        kind=original.kind,
        place_of_supply=original.place_of_supply,
        currency=original.currency,
        taxable_minor=taxable,
        cgst_minor=cgst,
        sgst_minor=sgst,
        igst_minor=igst,
        total_minor=taxable + cgst + sgst + igst,
        seller=original.seller,
        buyer=original.buyer,
        lines=[
            {
                "description": f"Credit for {original.number}: {source_line.get('description', '')}"[
                    :200
                ],
                "sac": source_line.get("sac", original.seller.get("sac")),
                "qty": 1,
                "unit": UNIT,
                "taxable_minor": taxable,
            }
        ],
        reverse_charge=False,
    )
    session.add(document)
    await session.flush()
    await record_audit(
        session,
        None,
        "billing.credit_note_issued",
        tenant_id=refund.tenant_id,
        tenant_label=await _tenant_name(session, refund.tenant_id),
        target_type="tax_document",
        target_id=str(document.id),
        details={
            "document_id": str(document.id),
            "number": number,
            "total_minor": document.total_minor,
        },
        actor=ledger.SYSTEM_ACTOR,
    )
    return document
