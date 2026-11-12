"""What a demo server refuses (pure decision), and the middleware that applies it.

A demo (`RAG_DEMO_MODE=true`) lets visitors try everything that matters and refuses what is
destructive, secret, outbound or costly. The decision is derived from `ROUTE_OPS`
(`api/permissions.py`): every `delete` and `manage` route is refused, so a route added later is
guarded by default through its own row. One short table adds the `write` routes that are secret
or outbound, and a second one lets the demo operator open the demo business's workspace.

The one credential that is never refused is the exact admin API key: a machine credential the
demo never shows and the nightly reset uses. A super-admin *session* is guarded like everyone's.
"""

import hmac
import logging
from collections.abc import Iterable
from typing import Any, Final

from callobuzz_rag.api.errors import APIError, error_response
from callobuzz_rag.api.permissions import PUBLIC_ROUTES, match_template, required_operation

logger = logging.getLogger(__name__)

CODE: Final = "demo_read_only"
MESSAGE: Final = (
    "This is a live demo, so this action is switched off. "
    "Everything else works, and the demo is reset every night."
)

# `manage` writes the demo operator may still do: open the demo business's workspace.
DEMO_ALLOWED: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("POST", "/api/v1/auth/switch-tenant"),
        ("POST", "/api/v1/auth/switch-admin"),
    }
)

# Public routes that create or change an account.
DEMO_REFUSED_PUBLIC: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("POST", "/api/v1/auth/signup"),
        ("POST", "/api/v1/auth/verify"),
        ("POST", "/api/v1/auth/resend"),
        ("POST", "/api/v1/auth/forgot"),
        ("POST", "/api/v1/auth/reset"),
        ("POST", "/api/v1/invitations/accept"),
        ("POST", "/api/v1/public/learn/{key}"),
        ("POST", "/api/v1/widget/lead"),
        ("POST", "/api/v1/public/landing/contact"),  # LP3 landing
    }
)

# Public routes refused for privacy, not for the account (a lead is a stranger's real contact
# details, and every demo owner sign-in could read it until the nightly reset).
DEMO_PRIVATE_PUBLIC: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("POST", "/api/v1/widget/lead"),
        ("POST", "/api/v1/public/landing/contact"),  # LP3 landing: a visitor's real contact details
    }
)

# Reads refused on a demo: everyone shares the two public sign-ins, so these would show each
# visitor the address and browser of the others.
DEMO_REFUSED_READS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("GET", "/api/v1/auth/sessions"),
        ("GET", "/api/v1/admin/audit"),
        ("GET", "/api/v1/admin/legal/acceptances"),  # P3 legal: sign-up e-mails
    }
)

# `write` routes that are secret, outbound or costly (reason in the log only).
DEMO_REFUSED: Final[dict[tuple[str, str], str]] = {
    ("POST", "/api/v1/billing/top-up-requests"): "billing",
    # P1 payments: no visitor starts a real payment on a demo
    ("POST", "/api/v1/billing/payments/orders"): "billing",
    ("POST", "/api/v1/billing/payments/orders/{order_id}/verify"): "billing",
    ("PUT", "/api/v1/billing/tax-details"): "billing",
    # /P1 payments
    # P5 plans: no visitor buys or cancels an add-on on a demo
    ("POST", "/api/v1/billing/add-ons/{add_on_id}/buy"): "billing",
    ("POST", "/api/v1/billing/add-ons/grants/{grant_id}/cancel"): "billing",
    # /P5 plans
    # P6 plan choice: no visitor moves the demo workspace to another plan
    ("POST", "/api/v1/billing/plan"): "billing",
    # /P6 plan choice
    ("POST", "/api/v1/corpora/{corpus_id}/widget-keys"): "keys",
    ("PATCH", "/api/v1/corpora/{corpus_id}/widget-keys/{key_id}"): "keys",
    ("POST", "/api/v1/webhooks"): "outbound",
    ("PATCH", "/api/v1/webhooks/{webhook_id}"): "outbound",
    ("POST", "/api/v1/webhooks/{webhook_id}/test"): "outbound",
    ("PATCH", "/api/v1/corpora/{corpus_id}/whatsapp"): "whatsapp",
    ("POST", "/api/v1/corpora/{corpus_id}/whatsapp/check"): "whatsapp",
    ("PUT", "/api/v1/corpora/{corpus_id}/whatsapp/documents"): "whatsapp",
    ("POST", "/api/v1/corpora/{corpus_id}/whatsapp/threads/{thread_id}/reply"): "whatsapp",
    ("PATCH", "/api/v1/corpora/{corpus_id}/whatsapp/threads/{thread_id}"): "whatsapp",
    ("POST", "/api/v1/corpora/{corpus_id}/eval/runs"): "cost",
    # stores a judge key and an endpoint the next visitor's judged run would call
    ("PUT", "/api/v1/corpora/{corpus_id}/eval/settings"): "secret",
    # starts a golden run of 15 to 25 answers (the same cost reason as eval/runs)
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/setup/{sid}/tests"): "cost",
}

# Body fields refused on routes the demo otherwise allows (a visitor's model key would be stored
# and then used by the next visitor). Checked in the route, never in the middleware.
DEMO_REFUSED_FIELDS: Final[dict[tuple[str, str], frozenset[str]]] = {
    ("POST", "/api/v1/corpora"): frozenset({"llm_config", "embedding_config"}),
    ("PUT", "/api/v1/corpora/{corpus_id}"): frozenset({"llm_config", "embedding_config"}),
}

_SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})


def refusal(method: str, path: str, *, admin_key: bool) -> str | None:
    """Why `method path` is refused on a demo server, or None when it is allowed.

    `admin_key` is True when the request carries exactly the engine's admin API key.
    """
    if admin_key:
        return None
    method = method.upper()
    if match_template(DEMO_REFUSED_READS, method, path) is not None:
        return "privacy"
    if method in _SAFE_METHODS:
        return None
    if match_template(DEMO_ALLOWED, method, path) is not None:
        return None
    public = match_template(PUBLIC_ROUTES, method, path)
    if public is not None:
        if public in DEMO_PRIVATE_PUBLIC:
            return "privacy"
        return "account" if public in DEMO_REFUSED_PUBLIC else None
    refused = match_template(DEMO_REFUSED, method, path)
    if refused is not None:
        return DEMO_REFUSED[refused]
    if method == "DELETE":
        return "delete"
    operation = required_operation(method, path)
    if operation in ("delete", "manage"):
        return operation
    return None


def refused_field(method: str, path: str, fields_set: Iterable[str]) -> str | None:
    """The first body field the demo refuses on `method path` (a template or a real path)."""
    row = match_template(DEMO_REFUSED_FIELDS, method, path)
    if row is None:
        return None
    sent = set(fields_set)
    for name in sorted(DEMO_REFUSED_FIELDS[row]):
        if name in sent:
            return name
    return None


def raise_for_fields(
    *,
    demo_mode: bool,
    method: str,
    template: str,
    fields_set: Iterable[str],
    admin_key: bool = False,
) -> None:
    """Raise the demo's 403 when a refused field is in the body (never in a normal install)."""
    if not demo_mode or admin_key:
        return
    name = refused_field(method, template, fields_set)
    if name is not None:
        logger.info("demo: refused field %s on %s %s (secret)", name, method, template)
        raise APIError(403, MESSAGE, CODE)


def is_admin_key(supplied: str | None, admin_key: str) -> bool:
    """True when `supplied` is exactly the admin key (constant-time; never for an empty key)."""
    if not supplied or not admin_key:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), admin_key.encode("utf-8"))


class DemoGuardMiddleware:
    """Pure ASGI (never `BaseHTTPMiddleware`: it cuts off streamed responses).

    Added immediately before the auth middleware, so it runs after authentication (an
    unauthenticated call still gets 401 first) and inside CORS (a refusal carries its headers).
    It reads only the method, the path and `X-API-Key`; the body is never touched.
    """

    def __init__(self, app: Any, admin_key: str) -> None:
        self.app = app
        self._admin_key = admin_key

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        supplied: str | None = None
        for name, value in scope.get("headers", ()):
            if name == b"x-api-key":
                supplied = value.decode("latin-1")
                break
        method, path = scope["method"], scope["path"]
        reason = refusal(method, path, admin_key=is_admin_key(supplied, self._admin_key))
        if reason is None:
            await self.app(scope, receive, send)
            return
        template = match_template(
            [*DEMO_REFUSED, *DEMO_REFUSED_PUBLIC, *DEMO_REFUSED_READS, *_all_templates()],
            method,
            path,
        )
        logger.info("demo: refused %s %s (%s)", method, template[1] if template else "?", reason)
        response = error_response(403, MESSAGE, CODE)
        await response(scope, receive, send)


def _all_templates() -> list[tuple[str, str]]:
    from callobuzz_rag.api.permissions import ROUTE_OPS

    return list(ROUTE_OPS)
