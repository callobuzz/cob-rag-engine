"""Signed links that show the unpublished draft (LP1, decision D16).

The editor shows the draft in an iframe. The page there is a normal GET, so it carries a token
instead of a credential: `base64url(json{p,l,exp}).base64url(hmac-sha256)` signed with the
engine's session secrets (any configured secret verifies, so a rotation does not break open
previews). Valid for 15 minutes, for one page and language.
"""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from callobuzz_rag.accounts.tokens import session_secrets

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings

TTL: Final = timedelta(minutes=15)


class PreviewTokenError(ValueError):
    """The token is malformed, forged or expired."""


@dataclass(frozen=True)
class PreviewClaims:
    page: str
    lang: str
    expires_at: datetime


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, body: str) -> str:
    return _b64(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())


def make_preview_token(
    settings: "Settings", *, page: str, lang: str, now: datetime | None = None
) -> str:
    moment = now or datetime.now(UTC)
    expires = int((moment + TTL).timestamp())
    body = _b64(
        json.dumps({"p": page, "l": lang, "exp": expires}, separators=(",", ":")).encode("utf-8")
    )
    return f"{body}.{_sign(session_secrets(settings)[0], body)}"


def read_preview_token(
    settings: "Settings", token: str, *, now: datetime | None = None
) -> PreviewClaims:
    moment = now or datetime.now(UTC)
    body, dot, signature = token.rpartition(".")
    if not dot or not body or not body.isascii():
        raise PreviewTokenError("malformed")
    if not any(
        hmac.compare_digest(_sign(secret, body), signature) for secret in session_secrets(settings)
    ):
        raise PreviewTokenError("bad signature")
    try:
        data = json.loads(_unb64(body))
        page, lang, expires = str(data["p"]), str(data["l"]), int(data["exp"])
    except (ValueError, KeyError, TypeError) as exc:
        raise PreviewTokenError("malformed") from exc
    if moment.timestamp() >= expires:
        raise PreviewTokenError("expired")
    return PreviewClaims(page=page, lang=lang, expires_at=datetime.fromtimestamp(expires, UTC))
