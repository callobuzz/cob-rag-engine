"""Meta's Graph API (WhatsApp Cloud API) client.

Safety rules, each pinned by `tests/unit/test_whatsapp_graph.py`:

- The base URL comes from settings only and must be `https://`; nothing in a request chooses a
  host. `phone_number_id` and `waba_id` must be digits before they go into a path.
- The access token travels only in the `Authorization` header: it is never logged and never
  part of an exception message. Meta's own error text is kept out of `str(error)` for the same
  reason (it can echo a token); the numeric Meta code is kept.
- `trust_env=False` (no proxy picked up from the environment), a 10 second timeout, and two
  retries with backoff on a timeout or a 5xx, for calls that are safe to repeat. A message send is
  not: the Cloud API takes no idempotency key, so a send is retried only when the request never
  left this machine (connect failure or pool timeout). After a read timeout or a 5xx Meta may
  already have accepted it, so the error carries `maybe_sent=True` and the caller must not resend.
  A 4xx is final.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from callobuzz_rag.core.exceptions import RAGKernelError

logger = logging.getLogger(__name__)

_ID = re.compile(r"^\d{5,20}$")
_TEMPLATE_NAME = re.compile(r"^[a-z0-9_]{1,512}$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}(_[A-Za-z]{2})?$")
_RETRIES = 2
_TIMEOUT_SECONDS = 10.0
_BACKOFF_SECONDS = (0.5, 1.5)
# Failures where the request provably never reached Meta, so a send can be repeated safely.
_NEVER_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
# "Re-engagement message": more than 24 hours since the customer's last message.
WINDOW_CLOSED_CODES = frozenset({131047})
# Meta throttling: 130429 (Cloud API throughput), 131056 (pair rate limit), 80007 (WABA rate
# limit). With HTTP 429 these mean "not sent, try again later".
THROTTLE_CODES = frozenset({130429, 131056, 80007})
DEFAULT_RETRY_AFTER_SECONDS = 60.0
MAX_RETRY_AFTER_SECONDS = 3600.0
MAX_BUTTONS = 3
MAX_BUTTON_TITLE = 20


def _retry_after(header: str | None) -> float:
    """Seconds to wait: Meta's `Retry-After` when it is a number, else a minute; capped."""
    try:
        seconds = float((header or "").strip())
    except ValueError:
        return DEFAULT_RETRY_AFTER_SECONDS
    if seconds != seconds or seconds <= 0:  # NaN or not positive
        return DEFAULT_RETRY_AFTER_SECONDS
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


class WhatsAppIdError(RAGKernelError, ValueError):
    """An id that is not digits was about to be put in a URL path."""


class GraphError(RAGKernelError):
    """Meta refused a request, or could not be reached. Never carries the token."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        meta_code: int | None = None,
        maybe_sent: bool = False,
        retryable: bool = False,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.status = status
        self.meta_code = meta_code
        self.maybe_sent = maybe_sent
        # True only for a throttle: Meta refused before sending, so the same call can be queued
        # again after `retry_after` seconds without any risk of a double message.
        self.retryable = retryable
        self.retry_after = retry_after

    @property
    def window_closed(self) -> bool:
        return self.meta_code in WINDOW_CLOSED_CODES


class GraphSettings(Protocol):
    whatsapp_graph_url: str
    whatsapp_graph_version: str
    whatsapp_app_id: str
    whatsapp_app_secret: str


def check_id(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise WhatsAppIdError("an id must be 5 to 20 digits")
    return value


class GraphClient:
    def __init__(
        self,
        *,
        settings: GraphSettings,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        base = settings.whatsapp_graph_url.rstrip("/")
        if not base.startswith("https://"):
            raise ValueError("the Graph API address must start with https://")
        if not re.fullmatch(r"v\d{1,3}\.\d{1,2}", settings.whatsapp_graph_version):
            raise ValueError("the Graph API version must look like v23.0")
        self._base = f"{base}/{settings.whatsapp_graph_version}"
        self._app_id = str(settings.whatsapp_app_id)
        self._app_secret = str(settings.whatsapp_app_secret)
        self._transport = transport
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: GraphSettings, **kw: Any) -> "GraphClient":
        return cls(settings=settings, **kw)

    # -- paths -------------------------------------------------------------------------------

    def message_url(self, phone_number_id: str) -> str:
        return f"{self._base}/{check_id(phone_number_id)}/messages"

    # -- transport ---------------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        json: Any = None,
        params: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        retry_unsent_only: bool = False,
    ) -> dict[str, Any]:
        # L5a demo: a demo server never calls Meta (refused before a request is built)
        from callobuzz_rag.demo import mode as demo_mode

        if demo_mode.is_demo():
            raise GraphError("demo mode: WhatsApp is off", maybe_sent=False)
        # /L5a demo
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        last: GraphError | None = None
        for attempt in range(_RETRIES + 1):
            if attempt:
                await self._sleep(_BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)])
            try:
                async with httpx.AsyncClient(
                    transport=self._transport, timeout=_TIMEOUT_SECONDS, trust_env=False
                ) as http:
                    resp = await http.request(
                        method,
                        url,
                        headers=headers,
                        json=json,
                        params=params,
                        data=data,
                        files=files,
                    )
            except httpx.HTTPError as exc:
                unsent = isinstance(exc, _NEVER_SENT)
                last = GraphError(
                    f"could not reach Meta ({type(exc).__name__})",
                    maybe_sent=retry_unsent_only and not unsent,
                )
                logger.warning("whatsapp graph %s failed: %s", method, type(exc).__name__)
                if retry_unsent_only and not unsent:
                    raise last from None
                continue
            if resp.status_code >= 500:
                logger.warning("whatsapp graph %s answered %s", method, resp.status_code)
                last = GraphError(
                    "Meta had a problem; it may or may not have sent the message"
                    if retry_unsent_only
                    else "Meta had a problem",
                    status=resp.status_code,
                    maybe_sent=retry_unsent_only,
                )
                if retry_unsent_only:
                    raise last
                continue
            try:
                body = resp.json()
            except ValueError:
                body = {}
            if resp.status_code >= 400:
                err = body.get("error") if isinstance(body, dict) else None
                code = err.get("code") if isinstance(err, dict) else None
                logger.info("whatsapp graph %s refused: %s code=%s", method, resp.status_code, code)
                meta_code = code if isinstance(code, int) else None
                if resp.status_code == 429 or meta_code in THROTTLE_CODES:
                    raise GraphError(
                        "Meta is rate limiting this number; try again later",
                        status=resp.status_code,
                        meta_code=meta_code,
                        maybe_sent=False,
                        retryable=True,
                        retry_after=_retry_after(resp.headers.get("retry-after")),
                    )
                raise GraphError(
                    "Meta refused the request", status=resp.status_code, meta_code=meta_code
                )
            return body if isinstance(body, dict) else {}
        if last is None:  # pragma: no cover
            raise GraphError("could not reach Meta")
        raise last

    async def _send(self, phone_number_id: str, token: str, payload: dict[str, Any]) -> str:
        out = await self._request(
            "POST",
            self.message_url(phone_number_id),
            token=token,
            json=payload,
            retry_unsent_only=True,
        )
        msgs = out.get("messages")
        if isinstance(msgs, list) and msgs and isinstance(msgs[0], dict):
            return str(msgs[0].get("id", ""))
        return ""

    @staticmethod
    def _envelope(to: str, kind: str, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": kind,
            kind: body,
        }

    # -- messages ----------------------------------------------------------------------------

    async def send_text(self, phone_number_id: str, *, token: str, to: str, text: str) -> str:
        payload = self._envelope(to, "text", {"body": text, "preview_url": False})
        return await self._send(phone_number_id, token, payload)

    async def send_document(
        self,
        phone_number_id: str,
        *,
        token: str,
        to: str,
        media_id: str,
        filename: str,
        caption: str = "",
    ) -> str:
        doc: dict[str, Any] = {"id": media_id, "filename": filename}
        if caption:
            doc["caption"] = caption
        return await self._send(phone_number_id, token, self._envelope(to, "document", doc))

    async def send_buttons(
        self,
        phone_number_id: str,
        *,
        token: str,
        to: str,
        body: str,
        buttons: list[tuple[str, str]],
    ) -> str:
        if not 1 <= len(buttons) <= MAX_BUTTONS:
            raise ValueError("a message has one to three buttons")
        if any(len(title) > MAX_BUTTON_TITLE or not title for _id, title in buttons):
            raise ValueError("a button title is 1 to 20 characters")
        interactive = {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": bid, "title": title}}
                    for bid, title in buttons
                ]
            },
        }
        return await self._send(
            phone_number_id, token, self._envelope(to, "interactive", interactive)
        )

    async def send_template(
        self,
        phone_number_id: str,
        *,
        token: str,
        to: str,
        name: str,
        language: str,
        params: list[str],
    ) -> str:
        if not _TEMPLATE_NAME.fullmatch(name):
            raise ValueError("a template name is lower-case letters, digits and underscores")
        if not _LANGUAGE.fullmatch(language):
            raise ValueError("a template language looks like en or en_US")
        template: dict[str, Any] = {"name": name, "language": {"code": language}}
        if params:
            template["components"] = [
                {"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}
            ]
        return await self._send(phone_number_id, token, self._envelope(to, "template", template))

    async def mark_read(self, phone_number_id: str, *, token: str, message_id: str) -> None:
        await self._request(
            "POST",
            self.message_url(phone_number_id),
            token=token,
            json={"messaging_product": "whatsapp", "status": "read", "message_id": message_id},
        )

    async def upload_media(
        self, phone_number_id: str, *, token: str, filename: str, content: bytes, mime: str
    ) -> str:
        out = await self._request(
            "POST",
            f"{self._base}/{check_id(phone_number_id)}/media",
            token=token,
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (filename, content, mime)},
        )
        return str(out.get("id", ""))

    # -- the number and the sign-up steps ----------------------------------------------------

    async def get_number(self, phone_number_id: str, *, token: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"{self._base}/{check_id(phone_number_id)}",
            token=token,
            params={"fields": "display_phone_number,verified_name,quality_rating"},
        )

    async def exchange_code(self, code: str) -> str:
        # The Graph API takes these as a POST form as well as a GET query. A POST keeps the app
        # secret out of the URL, so httpx's INFO request log (which prints the URL) never sees it,
        # with no global logger switched on and off while other requests run.
        out = await self._request(
            "POST",
            f"{self._base}/oauth/access_token",
            data={
                "client_id": self._app_id,
                "client_secret": self._app_secret,
                "code": code,
            },
        )
        return str(out.get("access_token", ""))

    async def register_number(self, phone_number_id: str, *, token: str, pin: str) -> None:
        await self._request(
            "POST",
            f"{self._base}/{check_id(phone_number_id)}/register",
            token=token,
            json={"messaging_product": "whatsapp", "pin": pin},
        )

    async def subscribe_app(self, waba_id: str, *, token: str) -> None:
        await self._request(
            "POST", f"{self._base}/{check_id(waba_id)}/subscribed_apps", token=token
        )

    async def unsubscribe_app(self, waba_id: str, *, token: str) -> None:
        await self._request(
            "DELETE", f"{self._base}/{check_id(waba_id)}/subscribed_apps", token=token
        )


class _EngineGraphSettings:
    """The engine's `Settings` as `GraphSettings` (the secret unwrapped only here)."""

    def __init__(self, settings: Any) -> None:
        self.whatsapp_graph_url = settings.whatsapp_graph_url
        self.whatsapp_graph_version = settings.whatsapp_graph_version
        self.whatsapp_app_id = settings.whatsapp_app_id
        self.whatsapp_app_secret = settings.whatsapp_app_secret.get_secret_value()


def graph_client(settings: Any, **kw: Any) -> GraphClient:
    """A client for the engine's own settings."""
    return GraphClient(settings=_EngineGraphSettings(settings), **kw)
