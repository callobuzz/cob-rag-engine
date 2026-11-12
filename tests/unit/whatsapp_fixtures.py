"""Offline Graph API for WhatsApp tests: nothing here touches Meta or the internet.

`FakeGraph` is an `httpx.MockTransport` that records every request (method, path, query, headers,
JSON or multipart body) and answers from a canned reply.
"""

import json as jsonlib
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class Recorded:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    json: Any = None
    raw: bytes = b""


@dataclass
class FakeGraph:
    reply: Any = field(default_factory=lambda: {"messages": [{"id": "wamid.OUT"}]})
    status: int = 200
    # (status, body) answered in order before falling back to `reply`.
    script: list[tuple[int, Any]] = field(default_factory=list)
    requests: list[Recorded] = field(default_factory=list)
    uploads: int = 0

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        body = request.content or b""
        parsed: Any = None
        if request.headers.get("content-type", "").startswith("application/json") and body:
            parsed = jsonlib.loads(body)
        self.requests.append(
            Recorded(
                request.method,
                request.url.path,
                dict(request.url.params),
                {k.lower(): v for k, v in request.headers.items()},
                parsed,
                body,
            )
        )
        if self.script:
            status, payload = self.script.pop(0)
        elif request.url.path.endswith("/media") and self.status < 400:
            self.uploads += 1  # a media upload answers with a fresh id
            status, payload = 200, {"id": f"MEDIA{self.uploads}"}
        else:
            status, payload = self.status, self.reply
        return httpx.Response(status, json=payload)


# ---------------------------------------------------------------- webhook payloads
# Shapes confirmed in the TV6 epic ("Confirmed shapes (T1)"): Meta's webhook for `messages`,
# `statuses` and, with coexistence, `smb_message_echoes`.

WABA = "5550001"


def _envelope(change: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": WABA, "changes": [change]}],
    }


def _metadata(phone_number_id: str) -> dict[str, str]:
    return {"display_phone_number": "919800000000", "phone_number_id": phone_number_id}


def inbound_message(
    message: dict[str, Any],
    *,
    phone_number_id: str = "1234567890",
    from_: str = "919822012345",
    name: str = "Asha",
) -> dict[str, Any]:
    return _envelope(
        {
            "field": "messages",
            "value": {
                "messaging_product": "whatsapp",
                "metadata": _metadata(phone_number_id),
                "contacts": [{"profile": {"name": name}, "wa_id": from_}],
                "messages": [message],
            },
        }
    )


def inbound_text(
    *,
    phone_number_id: str = "1234567890",
    wamid: str = "wamid.IN1",
    from_: str = "919822012345",
    text: str = "Root canal ka kitna charge hai?",
    timestamp: int = 1_790_000_000,
    name: str = "Asha",
) -> dict[str, Any]:
    return inbound_message(
        {
            "from": from_,
            "id": wamid,
            "timestamp": str(timestamp),
            "type": "text",
            "text": {"body": text},
        },
        phone_number_id=phone_number_id,
        from_=from_,
        name=name,
    )


def inbound_button(
    *,
    phone_number_id: str = "1234567890",
    wamid: str = "wamid.BTN1",
    from_: str = "919822012345",
    button_id: str = "optin_yes",
    title: str = "Yes, message me",
    timestamp: int = 1_790_000_000,
) -> dict[str, Any]:
    return inbound_message(
        {
            "from": from_,
            "id": wamid,
            "timestamp": str(timestamp),
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": button_id, "title": title},
            },
        },
        phone_number_id=phone_number_id,
        from_=from_,
    )


def inbound_image(
    *,
    phone_number_id: str = "1234567890",
    wamid: str = "wamid.IMG1",
    from_: str = "919822012345",
    timestamp: int = 1_790_000_000,
) -> dict[str, Any]:
    return inbound_message(
        {
            "from": from_,
            "id": wamid,
            "timestamp": str(timestamp),
            "type": "image",
            "image": {"id": "MEDIA1", "mime_type": "image/jpeg", "sha256": "x"},
        },
        phone_number_id=phone_number_id,
        from_=from_,
    )


def echo_text(
    *,
    phone_number_id: str = "1234567890",
    wamid: str = "wamid.ECHO1",
    to: str = "919822012345",
    text: str = "Hi Asha, this is the clinic.",
    timestamp: int = 1_790_000_100,
) -> dict[str, Any]:
    return _envelope(
        {
            "field": "smb_message_echoes",
            "value": {
                "messaging_product": "whatsapp",
                "metadata": _metadata(phone_number_id),
                "message_echoes": [
                    {
                        "from": "919800000000",
                        "to": to,
                        "id": wamid,
                        "timestamp": str(timestamp),
                        "type": "text",
                        "text": {"body": text},
                    }
                ],
            },
        }
    )


def status_update(
    *,
    phone_number_id: str = "1234567890",
    wamid: str = "wamid.OUT1",
    status: str = "delivered",
    recipient: str = "919822012345",
    timestamp: int = 1_790_000_050,
) -> dict[str, Any]:
    return _envelope(
        {
            "field": "messages",
            "value": {
                "messaging_product": "whatsapp",
                "metadata": _metadata(phone_number_id),
                "statuses": [
                    {
                        "id": wamid,
                        "recipient_id": recipient,
                        "status": status,
                        "timestamp": str(timestamp),
                    }
                ],
            },
        }
    )
