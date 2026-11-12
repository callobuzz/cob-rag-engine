"""Write a tax document's PDF to object storage once, and mail it.

Runs after the commit that issued the document (`worker/payment_tasks.py`), never inside it: a
PDF that cannot be written must not undo money (D10). The PDF goes to
`billing/tax-documents/<fy>/<document id>.pdf` through `storage.factory.object_storage` (the
storage garbage collector never touches `billing/`); its SHA-256 and key are stored on the frozen
row once, and a stored PDF is never overwritten (a second run finds it and keeps it). The sweep
calls `render_and_store` again for anything still missing, so a storage outage loses nothing.
"""

import hashlib
import io
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import select

from callobuzz_rag.billing.invoicing import pdf
from callobuzz_rag.billing.money import Money
from callobuzz_rag.db.models.payment import BillingProfile, Payment, PaymentRefund
from callobuzz_rag.db.models.tax_document import TaxDocument

logger = logging.getLogger(__name__)

PREFIX: Final = "billing/tax-documents"
SWEEP_AFTER: Final = timedelta(minutes=1)
MAIL_RETRY_WINDOW: Final = timedelta(days=1)
SWEEP_BATCH: Final = 50


def storage_key(document: TaxDocument) -> str:
    return f"{PREFIX}/{document.fy}/{document.id}.pdf"


async def _reference_and_original(session: Any, document: TaxDocument) -> tuple[str, Any]:
    """The provider id printed on the PDF and, for a credit note, the invoice it reduces."""
    if document.doc_type == "credit_note":
        refund = (
            await session.execute(
                select(PaymentRefund).where(PaymentRefund.id == document.refund_id)
            )
        ).scalar_one_or_none()
        original = (
            await session.execute(select(TaxDocument).where(TaxDocument.id == document.original_id))
        ).scalar_one_or_none()
        return (refund.provider_refund_id if refund else None) or "", original
    payment = (
        await session.execute(select(Payment).where(Payment.id == document.payment_id))
    ).scalar_one_or_none()
    return (payment.provider_payment_id if payment else "") or "", None


async def _recipients(session: Any, document: TaxDocument) -> list[str]:
    """The invoice e-mail address on the tax details, else the workspace's owners."""
    profile = (
        await session.execute(
            select(BillingProfile).where(BillingProfile.tenant_id == document.tenant_id)
        )
    ).scalar_one_or_none()
    if profile is not None and profile.email_for_invoices:
        return [profile.email_for_invoices]
    from callobuzz_rag.accounts.lifecycle import owner_emails

    return await owner_emails(session, document.tenant_id)


async def _locked(session: Any, document_id: uuid.UUID) -> TaxDocument | None:
    """The row under `FOR UPDATE`, fresh from the database: a concurrent run (the PDF task and the
    sweep) waits here, then finds the PDF and the e-mail already done and does nothing."""
    document: TaxDocument | None = (
        await session.execute(
            select(TaxDocument)
            .where(TaxDocument.id == document_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    return document


async def render_and_store(
    settings: Any,
    session_factory: Callable[[], Any],
    document_id: uuid.UUID,
    *,
    storage: Any,
    enqueue: Callable[..., None],
) -> str:
    """Make the PDF if it is missing, then queue the e-mail if it was not sent.

    Returns `missing` (no such document), `stored` (a PDF was written), `kept` (one was already
    there) or `mailed` (only the e-mail was outstanding). Raises when storage fails, leaving the
    row untouched: the sweep tries again.
    """
    outcome = "kept"
    async with session_factory() as session:
        document = await _locked(session, document_id)
        if document is None:
            return "missing"
        if document.pdf_key is None:
            key = storage_key(document)
            bucket = settings.s3_bucket
            if await storage.exists(bucket, key):  # a crash after upload: keep what is there
                data = await storage.download(bucket, key)
            else:
                reference, original = await _reference_and_original(session, document)
                data = pdf.render(document, reference=reference or None, original=original)
                await storage.upload(bucket, key, io.BytesIO(data), "application/pdf")
            document.pdf_key = key
            document.pdf_sha256 = hashlib.sha256(data).hexdigest()
            await session.commit()
            outcome = "stored"
            # the commit released the lock: take it again so a second runner that waited sees
            # `emailed_at` before we decide to mail
            document = await _locked(session, document_id)
            if document is None:
                return outcome
        if document.emailed_at is None:
            recipients = await _recipients(session, document)
            for to in recipients:
                enqueue(
                    "invoice_issued",
                    to,
                    mail_context(settings, document),
                    expires_s=7 * 24 * 3600,
                )
            document.emailed_at = datetime.now(UTC)  # nobody to mail also ends the retries
            await session.commit()
            if outcome == "kept":
                outcome = "mailed"
    return outcome


async def due_documents(session_factory: Callable[[], Any], now: datetime) -> list[uuid.UUID]:
    """Documents with no PDF yet, or no e-mail yet, a minute or more after they were issued."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(TaxDocument.id, TaxDocument.pdf_key, TaxDocument.created_at)
                .where(
                    TaxDocument.created_at < now - SWEEP_AFTER,
                    (TaxDocument.pdf_key.is_(None))
                    | (
                        TaxDocument.emailed_at.is_(None)
                        & (TaxDocument.created_at > now - MAIL_RETRY_WINDOW)
                    ),
                )
                .order_by(TaxDocument.created_at)
                .limit(SWEEP_BATCH)
            )
        ).all()
    return [row[0] for row in rows]


async def sweep(
    settings: Any,
    session_factory: Callable[[], Any],
    *,
    storage: Any,
    enqueue: Callable[..., None],
    now: datetime | None = None,
) -> dict[str, int]:
    """Fill every PDF (and e-mail) still outstanding. One failure never stops the others."""
    counts = {"stored": 0, "mailed": 0, "failed": 0}
    for document_id in await due_documents(session_factory, now or datetime.now(UTC)):
        try:
            outcome = await render_and_store(
                settings, session_factory, document_id, storage=storage, enqueue=enqueue
            )
        except Exception as exc:
            counts["failed"] += 1
            logger.error("Tax document %s: PDF failed (%s)", document_id, type(exc).__name__)
            continue
        if outcome in counts:
            counts[outcome] += 1
    return counts


async def queue_renders(session_factory: Callable[[], Any], payment_id: uuid.UUID) -> int:
    """Queue the PDF task for each document of `payment_id` that has none yet; returns how many.
    Best effort by design: the sweep covers whatever this misses."""
    from callobuzz_rag.worker.payment_tasks import render_tax_document_task

    async with session_factory() as session:
        ids = (
            (
                await session.execute(
                    select(TaxDocument.id).where(
                        TaxDocument.payment_id == payment_id, TaxDocument.pdf_key.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
    for document_id in ids:
        render_tax_document_task.delay(str(document_id))
    return len(ids)


async def send_mail(
    settings: Any,
    session_factory: Callable[[], Any],
    document_id: uuid.UUID,
    *,
    enqueue: Callable[..., None],
) -> int:
    """Queue the `invoice_issued` mail again (the operator's Resend); returns how many were queued.
    The row is never touched: `emailed_at` records the first send only."""
    async with session_factory() as session:
        document = await session.get(TaxDocument, document_id)
        if document is None:
            return 0
        recipients = await _recipients(session, document)
    for to in recipients:
        enqueue("invoice_issued", to, mail_context(settings, document), expires_s=7 * 24 * 3600)
    return len(recipients)


def mail_context(settings: Any, document: TaxDocument) -> dict[str, Any]:
    from callobuzz_rag.accounts.email.render import dashboard_link

    return {
        "tenant_name": (document.buyer or {}).get("legal_name") or "your workspace",
        "number": document.number,
        "total": Money(abs(document.total_minor), document.currency).format(),  # type: ignore[arg-type]
        "invoices_url": dashboard_link(settings.dashboard_url, "/plan"),
    }
