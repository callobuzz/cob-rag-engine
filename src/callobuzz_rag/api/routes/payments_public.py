"""The payment provider's webhook (P1): `POST /api/v1/public/payments/razorpay/webhook`.

Under `/api/v1/public/`, which takes no credential: the provider cannot send one. The request is
authenticated by `X-Razorpay-Signature`, the HMAC of the raw body under the webhook secret,
checked before anything is parsed; the body is capped at 1 MiB. The route stores the event under
its `X-Razorpay-Event-Id` (payload encrypted) and answers 200 at once: it makes no call to the
provider and writes no ledger entry. A worker fetches the truth and does the work
(`billing/payments/events.py`), so a forged or stale payload cannot move money.
"""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing.payments import events, factory
from callobuzz_rag.core.secrets import encryptor_from_settings

logger = logging.getLogger(__name__)

# P1 payments
router = APIRouter(prefix="/api/v1/public/payments", tags=["payments"])

MAX_BODY_BYTES = 1024 * 1024


async def _read_capped(request: Request) -> bytes:
    """The body as received, refusing more than `MAX_BODY_BYTES` before holding it all."""
    too_big = APIError(413, "That request is too large.", "payload_too_large")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise too_big
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise too_big
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/razorpay/webhook", summary="Receive payment events")
async def razorpay_webhook(
    request: Request, session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    """Payment, refund and dispute events. `X-Razorpay-Signature` must be the HMAC-SHA256 of the
    raw body under the webhook secret and `X-Razorpay-Event-Id` must be present (400
    `payments_signature_invalid` otherwise). Answers 200 `{}` once the event is stored; the same
    event id again is a no-op."""
    provider = await factory.provider_for(request.app, session)
    if provider is None:
        raise APIError(404, "Online payments are not switched on here.", "payments_disabled")
    raw = await _read_capped(request)
    invalid = APIError(400, "The signature is missing or wrong.", "payments_signature_invalid")
    if not provider.verify_webhook(raw, request.headers.get("x-razorpay-signature")):
        raise invalid
    event = provider.parse_webhook(raw, dict(request.headers))
    if event is None:
        raise invalid
    encryptor = encryptor_from_settings(request.app.state.settings)
    if encryptor is None:
        raise APIError(503, "Payments need RAG_ENCRYPTION_KEY.", "encryption_unavailable")
    stored = await events.store_event(session, encryptor, event, raw)
    await session.commit()
    if stored is not None:
        events.queue_event(stored.event_id)
    return JSONResponse({})


# /P1 payments
