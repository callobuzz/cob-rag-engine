"""Outbound webhook dispatcher with HMAC signing and retry logic.

Fire-and-forget: callers never block on webhook delivery. Failures are
logged but never propagate.
"""

import hashlib
import hmac
import json
import logging
import uuid
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.events import WebhookEvent, WebhookPayload

logger = logging.getLogger(__name__)


class WebhookDispatcher:
    """Dispatches webhook payloads to subscribed endpoints."""

    def __init__(self, settings: Settings, session_factory: Any) -> None:
        self._timeout = settings.webhook_timeout
        self._max_retries = settings.webhook_max_retries
        self._retry_delay = settings.webhook_retry_delay
        self._session_factory = session_factory
        # L5a demo: a demo server sends no webhooks
        self._demo = getattr(settings, "demo_mode", False) is True
        # /L5a demo

    async def dispatch(
        self, event: WebhookEvent, data: dict[str, Any], *, corpus_id: uuid.UUID | None = None
    ) -> None:
        """Send webhook to all enabled subscribers of this event.

        Lead and handoff events reach only the webhooks of `corpus_id`; every other event
        only the engine-wide ones. Never raises: all errors are logged and swallowed.
        """
        # L5a demo
        if self._demo:
            logger.info("demo: webhook %s not sent", event.value)
            return
        # /L5a demo
        try:
            configs = await self._get_subscribers(event, corpus_id)
        except Exception:
            logger.exception("Failed to fetch webhook configs for event=%s", event.value)
            return

        if not configs:
            logger.debug("No webhook subscribers for event=%s", event.value)
            return

        payload = WebhookPayload(event=event, data=data)

        for config in configs:
            try:
                await self._deliver(
                    url=config.url,
                    secret=config.secret,
                    payload=payload,
                )
                logger.info(
                    "Webhook delivered: event=%s url=%s",
                    event.value,
                    config.url,
                )
            except Exception:
                logger.exception(
                    "Webhook delivery failed after retries: event=%s url=%s webhook_id=%s",
                    event.value,
                    config.url,
                    config.id,
                )

    async def _get_subscribers(
        self, event: WebhookEvent, corpus_id: uuid.UUID | None = None
    ) -> list[Any]:
        """Fetch all enabled WebhookConfig rows subscribed to this event.

        A corpus-only event (lead, handoff) goes to that corpus's own webhooks, or to no one
        when no corpus is given; any other event goes to the engine-wide webhooks.
        """
        from sqlalchemy import select

        from callobuzz_rag.core.events import CORPUS_ONLY_EVENTS
        from callobuzz_rag.db.models.webhook_config import WebhookConfig

        stmt = (
            select(WebhookConfig)
            .where(WebhookConfig.enabled.is_(True))
            # ARRAY.Comparator.any takes a bare value; mypy resolves the
            # name to relationship PropComparator.any, which does not.
            .where(WebhookConfig.events.any(event.value))  # type: ignore[arg-type]
        )
        if event in CORPUS_ONLY_EVENTS:
            if corpus_id is None:
                return []
            stmt = stmt.where(WebhookConfig.corpus_id == corpus_id)
        else:
            stmt = stmt.where(WebhookConfig.corpus_id.is_(None))
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def _deliver(
        self,
        url: str,
        secret: str,
        payload: WebhookPayload,
    ) -> None:
        """Send a single webhook POST with HMAC signature and retry."""
        body = json.dumps(payload.to_dict(), separators=(",", ":"), sort_keys=True)
        signature = self._sign(body, secret)

        async with httpx.AsyncClient(timeout=self._timeout) as client:

            @retry(
                stop=stop_after_attempt(self._max_retries),
                wait=wait_exponential(
                    multiplier=self._retry_delay,
                    min=self._retry_delay,
                    max=self._retry_delay * 8,
                ),
                retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError)),
                reraise=True,
            )
            async def _send() -> None:
                response = await client.post(
                    url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Webhook-Signature": signature,
                        "X-Webhook-Event": payload.event.value,
                    },
                )
                response.raise_for_status()

            await _send()

    async def deliver_once(self, url: str, secret: str, payload: WebhookPayload) -> None:
        """One POST, no sleeping retries: the caller (a Celery task) owns the retry schedule
        so a failing endpoint never holds a worker slot. Raises on a non-2xx answer."""
        # L5a demo
        if self._demo:
            logger.info("demo: webhook %s not sent", payload.event.value)
            return
        # /L5a demo
        body = json.dumps(payload.to_dict(), separators=(",", ":"), sort_keys=True)
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client:
            response = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": self._sign(body, secret),
                    "X-Webhook-Event": payload.event.value,
                },
            )
            response.raise_for_status()

    @staticmethod
    def _sign(body: str, secret: str) -> str:
        """Compute HMAC-SHA256 hex digest of body using secret."""
        return hmac.new(
            secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
