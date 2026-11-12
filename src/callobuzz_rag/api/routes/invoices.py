"""Tax invoices: the owner's list and downloads, the operator's list, CSV and resend (P2).

`/api/v1/billing/invoices` is for a workspace owner (a member is refused 403 `owner_required`, a
knowledge-base key 403 `scope_forbidden`); `/api/v1/admin/billing/invoices` is the operator's. Every
route answers 404 `billing_disabled` while billing is off. A document is never created or changed
here: it is issued when a payment is captured (`billing/invoicing/documents.py`).
"""

import logging
import time
import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any, Final

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, require_admin
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.routes.billing_statements import owner_workspace
from callobuzz_rag.api.schemas.invoices import (
    AdminTaxDocumentList,
    AdminTaxDocumentRow,
    DocType,
    ResendOut,
    TaxDocumentList,
    TaxDocumentTotals,
    TaxDocumentView,
)
from callobuzz_rag.billing.config import require_billing_enabled
from callobuzz_rag.billing.invoicing import export, store
from callobuzz_rag.billing.invoicing.documents import local_date
from callobuzz_rag.db.models.payment import Payment
from callobuzz_rag.db.models.tax_document import TaxDocument
from callobuzz_rag.storage.factory import object_storage

logger = logging.getLogger(__name__)

RESEND_INTERVAL_S: Final = 600
PDF_RETRY_AFTER: Final = "5"

# P2 invoices
router = APIRouter(
    prefix="/api/v1/billing/invoices",
    tags=["invoices"],
    dependencies=[Depends(require_billing_enabled)],
)
admin_router = APIRouter(
    prefix="/api/v1/admin/billing/invoices",
    tags=["invoices"],
    dependencies=[Depends(require_admin), Depends(require_billing_enabled)],
)


def _not_found() -> APIError:
    return APIError(404, "That tax document does not exist.", "tax_document_not_found")


def _view(doc: TaxDocument, originals: dict[uuid.UUID, str]) -> TaxDocumentView:
    return TaxDocumentView(
        id=doc.id,
        doc_type=doc.doc_type,
        number=doc.number,
        issued_on=local_date(doc.issued_at),
        currency=doc.currency,
        taxable_minor=doc.taxable_minor,
        cgst_minor=doc.cgst_minor,
        sgst_minor=doc.sgst_minor,
        igst_minor=doc.igst_minor,
        total_minor=doc.total_minor,
        original_number=originals.get(doc.original_id) if doc.original_id else None,
        pdf_ready=doc.pdf_key is not None,
    )


async def _original_numbers(
    session: AsyncSession, docs: Sequence[TaxDocument]
) -> dict[uuid.UUID, str]:
    ids = {d.original_id for d in docs if d.original_id is not None}
    if not ids:
        return {}
    rows = (
        (await session.execute(select(TaxDocument).where(TaxDocument.id.in_(sorted(ids, key=str)))))
        .scalars()
        .all()
    )
    return {r.id: r.number for r in rows}


def _filename(doc: TaxDocument) -> str:
    return doc.number.replace("/", "-") + ".pdf"


async def _pdf_response(request: Request, doc: TaxDocument) -> Response:
    if doc.pdf_key is None:
        raise APIError(
            409,
            "Your invoice is still being prepared. Please try again in a few seconds.",
            "tax_document_pdf_pending",
            headers={"Retry-After": PDF_RETRY_AFTER},
        )
    settings = request.app.state.settings
    data = await object_storage(settings).download(settings.s3_bucket, doc.pdf_key)
    return Response(
        data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(doc)}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ------------------------------------------------------------------------------- the owner


@router.get("", response_model=TaxDocumentList)
async def list_my_invoices(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> TaxDocumentList:
    """Your tax invoices and credit notes, newest first. Owners only."""
    tenant_id = owner_workspace(request)
    mine = TaxDocument.tenant_id == tenant_id
    total = (
        await session.execute(select(func.count()).select_from(TaxDocument).where(mine))
    ).scalar_one()
    page = list(
        (
            await session.execute(
                select(TaxDocument)
                .where(mine)
                .order_by(TaxDocument.issued_at.desc(), TaxDocument.number.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    originals = await _original_numbers(session, page)
    return TaxDocumentList(items=[_view(d, originals) for d in page], total=int(total))


@router.get(
    "/{document_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def download_my_invoice(
    document_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    """The PDF of one of your tax documents. 409 `tax_document_pdf_pending` for a moment after
    the payment (retry in a few seconds). Owners only."""
    tenant_id = owner_workspace(request)
    doc = await session.get(TaxDocument, document_id)
    if doc is None or doc.tenant_id != tenant_id:
        raise _not_found()
    return await _pdf_response(request, doc)


# ---------------------------------------------------------------------------- the operator


def _filters(
    tenant_id: uuid.UUID | None,
    doc_type: DocType | None,
    fy: str | None,
    start: date | None,
    end: date | None,
) -> list[Any]:
    if start is not None and end is not None and start > end:
        raise APIError(422, "start must be on or before end", "invalid_range")
    where: list[Any] = []
    if tenant_id is not None:
        where.append(TaxDocument.tenant_id == tenant_id)
    if doc_type is not None:
        where.append(TaxDocument.doc_type == doc_type)
    if fy is not None:
        where.append(TaxDocument.fy == fy)
    if start is not None:
        where.append(TaxDocument.issued_at >= export.day_bounds(start, start)[0])
    if end is not None:
        where.append(TaxDocument.issued_at < export.day_bounds(end, end)[1])
    return where


@admin_router.get("", response_model=AdminTaxDocumentList)
async def list_invoices(
    tenant_id: uuid.UUID | None = Query(None, description="Only this workspace"),
    doc_type: DocType | None = Query(None, description="Only this kind of document"),
    fy: str | None = Query(
        None, pattern=r"^[0-9]{2}-[0-9]{2}$", description="Financial year, like 26-27"
    ),
    start: date | None = Query(None, description="First day (Indian time), inclusive"),
    end: date | None = Query(None, description="Last day (Indian time), inclusive"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> AdminTaxDocumentList:
    """Every tax document, newest first, with the totals of everything the filter matches
    (credit notes subtract). A deleted workspace's documents stay."""
    where = _filters(tenant_id, doc_type, fy, start, end)
    agg = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(TaxDocument.taxable_minor), 0),
                func.coalesce(func.sum(TaxDocument.cgst_minor), 0),
                func.coalesce(func.sum(TaxDocument.sgst_minor), 0),
                func.coalesce(func.sum(TaxDocument.igst_minor), 0),
                func.coalesce(func.sum(TaxDocument.total_minor), 0),
            ).where(*where)
        )
    ).one()
    page = list(
        (
            await session.execute(
                select(TaxDocument)
                .where(*where)
                .order_by(TaxDocument.issued_at.desc(), TaxDocument.number.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    originals = await _original_numbers(session, page)
    items = [
        AdminTaxDocumentRow(
            **_view(d, originals).model_dump(),
            tenant_id=d.tenant_id,
            buyer_name=str((d.buyer or {}).get("legal_name") or ""),
            buyer_gstin=(d.buyer or {}).get("gstin"),
            fy=d.fy,
            kind=d.kind,
            place_of_supply=d.place_of_supply,
            payment_id=d.payment_id,
        )
        for d in page
    ]
    return AdminTaxDocumentList(
        items=items,
        total=int(agg[0]),
        totals=TaxDocumentTotals(
            taxable_minor=int(agg[1]),
            cgst_minor=int(agg[2]),
            sgst_minor=int(agg[3]),
            igst_minor=int(agg[4]),
            total_minor=int(agg[5]),
        ),
    )


@admin_router.get(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
async def export_invoices(
    start: date = Query(..., description="First day (Indian time), inclusive"),
    end: date = Query(..., description="Last day (Indian time), inclusive"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """The CSV your accountant files returns from: one row per document in the range (at most
    366 days). Amounts are decimals like 1178.82; a credit note's are negative."""
    if start > end:
        raise APIError(422, "start must be on or before end", "invalid_range")
    if (end - start).days + 1 > export.MAX_DAYS:
        raise APIError(422, "Pick a range of at most 366 days.", "range_too_long")
    docs = list(
        (
            await session.execute(
                select(TaxDocument)
                .where(*_filters(None, None, None, start, end))
                .order_by(TaxDocument.issued_at, TaxDocument.number)
            )
        )
        .scalars()
        .all()
    )
    originals = await _original_numbers(session, docs)
    payment_ids = sorted({d.payment_id for d in docs}, key=str)
    references: dict[uuid.UUID, str] = {}
    if payment_ids:
        references = {
            p.id: p.provider_payment_id
            for p in (
                (await session.execute(select(Payment).where(Payment.id.in_(payment_ids))))
                .scalars()
                .all()
            )
        }
    body = export.render_csv(docs, originals=originals, references=references)
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="tax-documents-{start}-{end}.csv"',
            "Cache-Control": "private, no-store",
        },
    )


@admin_router.get(
    "/{document_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def download_invoice(
    document_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    """The PDF of any tax document, also a deleted workspace's."""
    doc = await session.get(TaxDocument, document_id)
    if doc is None:
        raise _not_found()
    return await _pdf_response(request, doc)


def _redis(request: Request) -> Any:
    client = getattr(request.app.state, "invoices_redis", None)
    if client is None:
        import redis.asyncio as aioredis

        client = aioredis.from_url(  # type: ignore[no-untyped-call]
            request.app.state.settings.redis_url, socket_timeout=2, socket_connect_timeout=2
        )
        request.app.state.invoices_redis = client
    return client


@admin_router.post("/{document_id}/resend", response_model=ResendOut, status_code=202)
async def resend_invoice(
    document_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> ResendOut:
    """E-mail the document again to the invoice address (else the owners). Once every 10
    minutes per document."""
    doc = await session.get(TaxDocument, document_id)
    if doc is None:
        raise _not_found()
    try:
        taken = await _redis(request).set(
            f"invoice-resend:{document_id}", str(int(time.time())), nx=True, ex=RESEND_INTERVAL_S
        )
    except Exception as exc:  # fail open: a resend is harmless
        logger.warning("Resend limit unavailable (%s)", type(exc).__name__)
        taken = True
    if not taken:
        raise APIError(
            429,
            "This invoice was sent a moment ago. Please wait a few minutes.",
            "invoice_resend_too_soon",
            headers={"Retry-After": str(RESEND_INTERVAL_S)},
        )
    from callobuzz_rag.accounts.email.dispatch import enqueue_email

    await store.send_mail(
        request.app.state.settings,
        request.app.state.session_factory,
        document_id,
        enqueue=enqueue_email,
    )
    return ResendOut()


# /P2 invoices
