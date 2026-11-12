"""The seams where tax documents (P2) hook into a payment.

`capture.apply_captured` calls `on_payment_captured` inside the capture transaction (the tax
invoice is numbered and stored there, so a rollback returns its number) and `refunds.settle`
calls `on_refund_processed` when a refund is processed (the credit note). P2 fills the bodies;
until then they do nothing.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.db.models.payment import Payment, PaymentOrder, PaymentRefund


async def on_payment_captured(
    session: AsyncSession,
    payment: Payment,
    order: PaymentOrder,
    *,
    now: datetime | None = None,
) -> None:
    """Issue the tax invoice for a captured payment. Runs in the capture transaction, after the
    ledger entry and before the commit. The invoice is dated by the moment it is processed, never
    by the provider's payment time: a late webhook must not number into a closed year."""
    # P2 invoices
    from callobuzz_rag.billing.invoicing import documents

    await documents.issue_invoice(session, payment, order, now=now or datetime.now(UTC))
    # /P2 invoices


async def on_refund_processed(session: AsyncSession, refund: PaymentRefund) -> None:
    """Issue the credit note for a processed refund. Runs in the refund's own transaction."""
    # P2 invoices
    from callobuzz_rag.billing.invoicing import documents

    await documents.issue_credit_note(session, refund, now=refund.processed_at or datetime.now(UTC))
    # /P2 invoices
