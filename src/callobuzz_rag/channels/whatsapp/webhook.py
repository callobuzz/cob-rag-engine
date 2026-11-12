"""Meta's webhook for WhatsApp: the signature and the parser (TV6).

Both are pure. The shapes are the ones confirmed in the epic's "Confirmed shapes (T1)":

- `X-Hub-Signature-256: sha256=<hex>`, the HMAC-SHA256 of the **raw body** keyed with the app
  secret. Compared in constant time on the bytes as received, never on re-serialised JSON.
- `entry[].changes[]` with `field: "messages"` (`value.messages[]`, `value.statuses[]`,
  `value.contacts[]`, `value.metadata.phone_number_id`) or `field: "smb_message_echoes"`
  (`value.message_echoes[]`, the owner's messages sent from the WhatsApp Business app).

`parse` never raises on a payload it does not understand: unknown fields and malformed entries
are skipped, because a webhook that errors is retried by Meta for days.
"""

import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

_SIGNATURE = re.compile(r"^sha256=([0-9a-fA-F]{64})$")
_DIGITS = re.compile(r"^\d{5,20}$")
MAX_TEXT_CHARS = 4096

EventKind = Literal["message", "echo", "status", "other"]


def verify_signature(raw: bytes, header: str | None, secret: str) -> bool:
    """True when `header` is `sha256=` plus the HMAC-SHA256 of `raw` under `secret`."""
    if not header or not secret:
        return False
    match = _SIGNATURE.fullmatch(header.strip())
    if match is None:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(match.group(1).lower(), expected)


def verify_challenge(params: dict[str, str], verify_token: str) -> str | None:
    """The `hub.challenge` to answer, or None when the verification request is refused: mode
    `subscribe`, the right token (constant-time), and a challenge that is only digits (Meta
    sends an integer; anything else is not echoed back)."""
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token") or ""
    challenge = params.get("hub.challenge") or ""
    if mode != "subscribe" or not verify_token:
        return None
    if not hmac.compare_digest(token.encode("utf-8"), verify_token.encode("utf-8")):
        return None
    if not re.fullmatch(r"\d{1,20}", challenge):
        return None
    return challenge


@dataclass(frozen=True)
class ParsedEvent:
    """One thing Meta told us, reduced to what the engine uses."""

    kind: EventKind
    phone_number_id: str
    external_id: str  # the wamid, or `status:<wamid>:<status>`
    contact: str  # the customer's number, digits only ("919822012345"); "" for `other`
    profile_name: str | None
    timestamp: datetime | None
    message_type: str  # text | interactive | image | ... | status
    text: str  # the text of a text message, or the title of a tapped button
    button_id: str | None
    status: str | None = None  # sent | delivered | read | failed | deleted
    error_code: int | None = None

    def to_payload(self) -> dict[str, Any]:
        """What is stored (encrypted): no media ids, nothing the engine does not read."""
        return {
            "kind": self.kind,
            "type": self.message_type,
            "text": self.text,
            "button_id": self.button_id,
            "name": self.profile_name,
            "contact": self.contact,
            "ts": self.timestamp.isoformat() if self.timestamp else None,
            "status": self.status,
            "error_code": self.error_code,
        }


def _digits(value: Any) -> str:
    text = str(value or "")
    return text if _DIGITS.fullmatch(text) else ""


def _when(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(str(value)), tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _text_of(message: dict[str, Any]) -> tuple[str, str | None]:
    """(text, button id) of a message: the body of a text, the title of a tapped button or list
    row, else empty."""
    kind = message.get("type")
    if kind == "text":
        body = message.get("text")
        text = str(body.get("body", "")) if isinstance(body, dict) else ""
        return text[:MAX_TEXT_CHARS], None
    if kind == "interactive":
        inter = message.get("interactive")
        if isinstance(inter, dict):
            reply = inter.get("button_reply") or inter.get("list_reply")
            if isinstance(reply, dict):
                return str(reply.get("title", ""))[:MAX_TEXT_CHARS], str(reply.get("id", ""))[:256]
    if kind == "button":  # a template's quick-reply button
        button = message.get("button")
        if isinstance(button, dict):
            return (
                str(button.get("text", ""))[:MAX_TEXT_CHARS],
                str(button.get("payload", ""))[:256] or None,
            )
    return "", None


def _as_dicts(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def parse(payload: Any) -> list[ParsedEvent]:
    """Every message, echo and status in a webhook body, in order."""
    out: list[ParsedEvent] = []
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return out
    for entry in _as_dicts(payload.get("entry")):
        for change in _as_dicts(entry.get("changes")):
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            meta = value.get("metadata")
            number_id = _digits(meta.get("phone_number_id")) if isinstance(meta, dict) else ""
            if not number_id:
                continue
            field = change.get("field")
            names = {
                _digits(c.get("wa_id")): (c.get("profile") or {}).get("name")
                for c in _as_dicts(value.get("contacts"))
                if isinstance(c.get("profile"), dict)
            }
            if field == "messages":
                for message in _as_dicts(value.get("messages")):
                    contact = _digits(message.get("from"))
                    wamid = str(message.get("id") or "")[:128]
                    if not contact or not wamid:
                        continue
                    text, button_id = _text_of(message)
                    out.append(
                        ParsedEvent(
                            kind="message",
                            phone_number_id=number_id,
                            external_id=wamid,
                            contact=contact,
                            profile_name=_name(names.get(contact)),
                            timestamp=_when(message.get("timestamp")),
                            message_type=str(message.get("type") or "unknown")[:20],
                            text=text,
                            button_id=button_id,
                        )
                    )
                for status in _as_dicts(value.get("statuses")):
                    wamid = str(status.get("id") or "")[:100]
                    state = str(status.get("status") or "")[:16]
                    if not wamid or not state:
                        continue
                    errors = _as_dicts(status.get("errors"))
                    code = errors[0].get("code") if errors else None
                    out.append(
                        ParsedEvent(
                            kind="status",
                            phone_number_id=number_id,
                            external_id=f"status:{wamid}:{state}",
                            contact=_digits(status.get("recipient_id")),
                            profile_name=None,
                            timestamp=_when(status.get("timestamp")),
                            message_type="status",
                            text="",
                            button_id=None,
                            status=state,
                            error_code=code if isinstance(code, int) else None,
                        )
                    )
            elif field == "smb_message_echoes":
                for message in _as_dicts(value.get("message_echoes")):
                    contact = _digits(message.get("to"))
                    wamid = str(message.get("id") or "")[:128]
                    if not contact or not wamid:
                        continue
                    text, _ = _text_of(message)
                    out.append(
                        ParsedEvent(
                            kind="echo",
                            phone_number_id=number_id,
                            external_id=wamid,
                            contact=contact,
                            profile_name=None,
                            timestamp=_when(message.get("timestamp")),
                            message_type=str(message.get("type") or "unknown")[:20],
                            text=text,
                            button_id=None,
                        )
                    )
    return out


def _name(value: Any) -> str | None:
    text = str(value).strip() if value else ""
    return text[:100] or None
