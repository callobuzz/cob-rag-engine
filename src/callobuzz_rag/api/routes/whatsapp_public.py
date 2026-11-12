"""Meta's webhook for WhatsApp: `GET` verifies the address, `POST` delivers events (TV6).

Both live under `/api/v1/public/`, which takes no credential: Meta cannot send one. The `POST`
is authenticated by the signature over the raw body, checked before anything is parsed; the
body is capped at 1 MiB. Events are stored (payload encrypted) and answered with 200 at once; a
worker answers the customer. Nothing here talks to Meta or to a model.
"""

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.channels.whatsapp import dispatch, webhook
from callobuzz_rag.channels.whatsapp.availability import engine_ready
from callobuzz_rag.channels.whatsapp.store import store_events
from callobuzz_rag.conversation.leads.crypto import (
    LeadCryptoUnavailableError,
    lead_crypto_from_settings,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/public/whatsapp", tags=["whatsapp"])

MAX_BODY_BYTES = 1024 * 1024


def _ready(request: Request) -> None:
    if not engine_ready(request.app.state.settings):
        raise APIError(404, "WhatsApp is not set up on this engine.", "whatsapp_not_enabled")


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


@router.get("/webhook", response_class=PlainTextResponse, summary="Verify the webhook address")
async def verify(request: Request) -> PlainTextResponse:
    """Meta's one-time check when the webhook address is saved: `hub.mode=subscribe`, our
    `hub.verify_token`, and a `hub.challenge` that is echoed back as plain text."""
    _ready(request)
    settings = request.app.state.settings
    challenge = webhook.verify_challenge(
        dict(request.query_params), settings.whatsapp_verify_token.get_secret_value()
    )
    if challenge is None:
        raise APIError(403, "The verification token is wrong.", "whatsapp_verify_failed")
    return PlainTextResponse(challenge)


@router.post("/webhook", summary="Receive WhatsApp events")
async def receive(request: Request, session: AsyncSession = Depends(get_session)) -> JSONResponse:
    """Messages, delivery statuses and (with coexistence) the owner's own messages. The
    `X-Hub-Signature-256` header must be the HMAC-SHA256 of the raw body under the Meta app
    secret (401 `whatsapp_signature_invalid`). Answers 200 `{}` once the events are stored."""
    _ready(request)
    settings = request.app.state.settings
    raw = await _read_capped(request)
    if not webhook.verify_signature(
        raw,
        request.headers.get("x-hub-signature-256"),
        settings.whatsapp_app_secret.get_secret_value(),
    ):
        raise APIError(401, "The signature is missing or wrong.", "whatsapp_signature_invalid")
    try:
        payload = json.loads(raw)
    except ValueError:
        return JSONResponse({})  # signed but not JSON: nothing to keep, nothing to retry
    events = webhook.parse(payload)
    if not events:
        return JSONResponse({})
    try:
        crypto = lead_crypto_from_settings(settings)
    except LeadCryptoUnavailableError as exc:
        raise APIError(503, "WhatsApp needs RAG_ENCRYPTION_KEY.", "encryption_unavailable") from exc
    woken = await store_events(session, crypto, events)
    await session.commit()
    for thread_id in woken:
        dispatch.queue_thread(thread_id)
    return JSONResponse({})
