"""Withdrawal links.

The link carries `<lead id>.<secret>`; the lead row keeps only `sha256(secret)`
(`leads.withdraw_hash`). Nothing in the link depends on RAG_ENCRYPTION_KEY, so rotating that
key (and dropping the old one) cannot kill a link a visitor holds for up to ten years, and a
database leak does not hand out working links. The token travels in the URL fragment
(`/privacy/withdraw#t=...`), which browsers never send to a server, so it stays out of access
logs. The link base must be `RAG_PUBLIC_URL`: behind a proxy the request's own base URL is an
internal host.
"""

import hashlib
import hmac
import secrets
import uuid

from callobuzz_rag.core.exceptions import RAGKernelError


class WithdrawTokenError(RAGKernelError):
    """Malformed withdrawal token."""


def new_withdraw_secret() -> str:
    return secrets.token_urlsafe(32)


def secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def make_withdraw_token(lead_id: uuid.UUID, secret: str) -> str:
    return f"{lead_id}.{secret}"


def read_withdraw_token(token: str) -> tuple[uuid.UUID, str]:
    lead_part, dot, secret = token.partition(".")
    if not dot or not secret:
        raise WithdrawTokenError("not a withdrawal token")
    try:
        return uuid.UUID(lead_part), secret
    except ValueError as e:
        raise WithdrawTokenError("not a withdrawal token") from e


def secret_matches(stored_hash: str | None, secret: str) -> bool:
    """Constant-time; a lead with no stored hash (no link was ever issued) never matches."""
    if not stored_hash:
        return False
    return hmac.compare_digest(stored_hash, secret_hash(secret))


def withdraw_url(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/privacy/withdraw#t={token}"
