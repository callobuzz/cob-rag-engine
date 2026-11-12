"""Webhook CRUD endpoints — manage outbound webhook subscriptions."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, require_admin
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.webhooks import (
    WebhookCreateRequest,
    WebhookListResponse,
    WebhookResponse,
    WebhookTestResponse,
    WebhookUpdateRequest,
)
from callobuzz_rag.core.events import CORPUS_ONLY_EVENTS, WebhookEvent, WebhookPayload
from callobuzz_rag.db.models.webhook_config import WebhookConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


@router.post("", response_model=WebhookResponse, status_code=201)
async def create_webhook(
    body: WebhookCreateRequest,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin),
) -> WebhookResponse:
    """Register a new webhook subscription."""
    refused = sorted(e.value for e in body.events if e in CORPUS_ONLY_EVENTS)
    if refused:
        raise APIError(
            422,
            "Lead and handoff events carry contact data and go only to a knowledge base's own "
            "webhooks (/api/v1/corpora/{corpus_id}/webhooks)",
            "webhook_event_not_allowed",
            events=refused,
        )
    webhook = WebhookConfig(
        url=str(body.url),
        events=[e.value for e in body.events],
        secret=body.secret,
        enabled=True,
    )
    session.add(webhook)
    await session.commit()
    await session.refresh(webhook)

    logger.info(
        "Webhook created: id=%s url=%s events=%s",
        webhook.id,
        webhook.url,
        webhook.events,
    )

    return WebhookResponse(
        id=webhook.id,
        url=webhook.url,
        events=webhook.events,
        enabled=webhook.enabled,
        created_at=webhook.created_at,
        updated_at=webhook.updated_at,
    )


@router.get("", response_model=WebhookListResponse)
async def list_webhooks(
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin),
) -> WebhookListResponse:
    """List the engine-wide webhook subscriptions (a knowledge base's own are listed under
    /api/v1/corpora/{corpus_id}/webhooks)."""
    stmt = (
        select(WebhookConfig)
        .where(WebhookConfig.corpus_id.is_(None))
        .order_by(WebhookConfig.created_at.desc())
    )
    result = await session.execute(stmt)
    webhooks = result.scalars().all()

    return WebhookListResponse(
        webhooks=[
            WebhookResponse(
                id=w.id,
                url=w.url,
                events=w.events,
                enabled=w.enabled,
                created_at=w.created_at,
                updated_at=w.updated_at,
            )
            for w in webhooks
        ],
        count=len(webhooks),
    )


def _response(w: WebhookConfig) -> WebhookResponse:
    return WebhookResponse(
        id=w.id,
        url=w.url,
        events=w.events,
        enabled=w.enabled,
        created_at=w.created_at,
        updated_at=w.updated_at,
    )


@router.patch("/{webhook_id}", response_model=WebhookResponse)
async def update_webhook(
    webhook_id: UUID,
    body: WebhookUpdateRequest,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin),
) -> WebhookResponse:
    """Enable or disable a webhook. A disabled webhook receives no events."""
    webhook = await session.get(WebhookConfig, webhook_id)
    if not webhook:
        raise HTTPException(status_code=404, detail=f"Webhook {webhook_id} not found")
    webhook.enabled = body.enabled
    await session.commit()
    await session.refresh(webhook)
    logger.info("Webhook %s %s", webhook_id, "enabled" if body.enabled else "disabled")
    return _response(webhook)


@router.delete("/{webhook_id}", status_code=204)
async def delete_webhook(
    webhook_id: UUID,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin),
) -> None:
    """Delete a webhook subscription."""
    webhook = await session.get(WebhookConfig, webhook_id)
    if not webhook:
        raise HTTPException(status_code=404, detail=f"Webhook {webhook_id} not found")

    await session.delete(webhook)
    await session.commit()
    logger.info("Webhook deleted: id=%s", webhook_id)


@router.post("/{webhook_id}/test", response_model=WebhookTestResponse)
async def test_webhook(
    webhook_id: UUID,
    session: AsyncSession = Depends(get_session),
    _: None = Depends(require_admin),
) -> WebhookTestResponse:
    """Send a test event to a specific webhook endpoint."""
    import hashlib
    import hmac
    import json

    import httpx

    webhook = await session.get(WebhookConfig, webhook_id)
    if not webhook or webhook.corpus_id is not None:
        # A knowledge base's webhook belongs to its owner: an operator test event must not
        # be posted into a tenant's endpoint.
        raise HTTPException(status_code=404, detail=f"Webhook {webhook_id} not found")

    payload = WebhookPayload(
        event=WebhookEvent.CONFIG_CHANGED,
        data={"test": True, "webhook_id": str(webhook_id)},
    )
    body = json.dumps(payload.to_dict(), separators=(",", ":"), sort_keys=True)
    signature = hmac.new(
        webhook.secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                webhook.url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": signature,
                    "X-Webhook-Event": payload.event.value,
                },
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Webhook endpoint returned {e.response.status_code}",
        ) from e
    except httpx.ConnectError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Could not connect to webhook URL: {webhook.url}",
        ) from e
    except httpx.HTTPError as e:
        # Timeouts, protocol and redirect errors: name the class only, since the raw
        # message can carry internal addresses.
        raise HTTPException(
            status_code=502,
            detail=f"Webhook delivery failed: {type(e).__name__}",
        ) from e

    return WebhookTestResponse(status="sent", message="Test event dispatched successfully")
