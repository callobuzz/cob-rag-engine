"""What each route needs of an API key: one table, enforced in the auth layer.

An API key (`ck_` or `tk_`) has a permission: `read`, `write` or `full`. Every authenticated
route is classified here, by method and path template, as one of four operations:

- `read`: search, ask, list, get. Includes `POST /query*` and a conversation turn.
- `write`: add or change content and non-destructive settings.
- `delete`: remove content, a knowledge base, a document, a widget key.
- `manage`: API keys, teams, invitations, plans and engine settings.

`read` allows `read`; `write` allows `read` and `write`; `full` allows all four. The
middleware (`api/auth.py`) calls `check_key_permission` once a key has authenticated, so a
route needs no per-route code and a refused request does nothing. A path that is in no row
needs `manage` (default deny). `tests/unit/test_key_permissions.py` walks the app's OpenAPI
paths and fails when a non-public route is missing from `ROUTE_OPS`, or a row names a route
that no longer exists.

The admin key and dashboard sessions carry no permission and are not checked here (sessions
keep the owner and member rules of `api/deps.py`).
"""

import re
import uuid
from collections.abc import Iterable  # L5a demo
from dataclasses import dataclass
from typing import Final, Literal

Operation = Literal["read", "write", "delete", "manage"]
Permission = Literal["read", "write", "full"]

PERMISSIONS: Final[tuple[Permission, ...]] = ("read", "write", "full")
DEFAULT_AGENT_PERMISSION: Final[Permission] = "read"

_ALLOWS: Final[dict[str, frozenset[str]]] = {
    "read": frozenset({"read"}),
    "write": frozenset({"read", "write"}),
    "full": frozenset({"read", "write", "delete", "manage"}),
}
_NEEDS: Final[dict[str, Permission]] = {
    "read": "read",
    "write": "write",
    "delete": "full",
    "manage": "full",
}

# No credential, or their own (a widget key, a login): not classified, not checked.
PUBLIC_ROUTES: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("GET", "/api/v1/health"),
        ("GET", "/api/v1/health/ready"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/refresh"),
        ("POST", "/api/v1/auth/logout"),
        ("POST", "/api/v1/auth/reset"),
        ("POST", "/api/v1/auth/signup"),
        ("POST", "/api/v1/auth/verify"),
        ("POST", "/api/v1/auth/resend"),
        ("POST", "/api/v1/auth/forgot"),
        ("POST", "/api/v1/invitations/view"),
        ("POST", "/api/v1/invitations/accept"),
        ("GET", "/api/v1/public/branding"),
        ("POST", "/api/v1/public/payments/razorpay/webhook"),  # P1 payments
        ("GET", "/api/v1/public/legal"),  # P3 legal
        ("GET", "/api/v1/public/legal/{slug}"),  # P3 legal
        ("GET", "/api/v1/public/avatars/{name}"),  # BIZ4
        ("GET", "/api/v1/public/price-sheet/{token}"),  # TV3
        ("GET", "/api/v1/public/plans"),  # P6 plan choice
        ("POST", "/api/v1/public/cob/chat"),  # COB6
        ("POST", "/api/v1/widget/chat"),
        ("POST", "/api/v1/widget/event"),
        ("POST", "/api/v1/widget/handoff"),
        ("GET", "/api/v1/widget/config"),
        ("POST", "/api/v1/widget/lead"),
        ("POST", "/api/v1/public/landing/contact"),  # LP3 landing
        ("POST", "/api/v1/public/leads/withdraw"),
        ("POST", "/api/v1/public/learn/{key}"),  # BIZ2
        ("GET", "/api/v1/public/learn/{key}"),  # BIZ2
        ("POST", "/api/v1/widget/page"),  # BIZ2
        ("GET", "/api/v1/public/whatsapp/webhook"),  # TV6
        ("POST", "/api/v1/public/whatsapp/webhook"),  # TV6
    }
)


@dataclass(frozen=True)
class KeyInfo:
    """The API key a request authenticated with (`request.state.api_key`)."""

    id: uuid.UUID
    name: str
    permission: str
    kind: Literal["ck", "tk"]


ROUTE_OPS: Final[dict[tuple[str, str], Operation]] = {
    ("GET", "/api/v1/admin/audit"): "manage",
    # P3 legal
    ("GET", "/api/v1/admin/legal"): "manage",
    ("GET", "/api/v1/admin/legal/settings"): "manage",
    ("PUT", "/api/v1/admin/legal/settings"): "manage",
    ("GET", "/api/v1/admin/legal/acceptances"): "manage",
    ("GET", "/api/v1/admin/legal/{slug}"): "manage",
    ("PUT", "/api/v1/admin/legal/{slug}/draft"): "manage",
    ("POST", "/api/v1/admin/legal/{slug}/preview"): "manage",
    ("POST", "/api/v1/admin/legal/{slug}/publish"): "manage",
    # /P3 legal
    # LP1 landing
    ("GET", "/api/v1/admin/landing"): "manage",
    ("PUT", "/api/v1/admin/landing/draft"): "manage",
    ("PUT", "/api/v1/admin/landing/settings"): "manage",
    ("POST", "/api/v1/admin/landing/theme/preview"): "manage",
    ("POST", "/api/v1/admin/landing/preview-link"): "manage",
    ("POST", "/api/v1/admin/landing/assets"): "manage",
    ("DELETE", "/api/v1/admin/landing/assets/{asset_id}"): "manage",
    ("POST", "/api/v1/admin/landing/publish"): "manage",
    ("GET", "/api/v1/admin/landing/versions"): "manage",
    ("POST", "/api/v1/admin/landing/versions/{version}/restore"): "manage",
    # /LP1 landing
    # LP2 landing
    ("GET", "/api/v1/admin/landing/presets"): "manage",
    ("GET", "/api/v1/admin/landing/presets/{key}"): "manage",
    ("POST", "/api/v1/admin/landing/generate"): "manage",
    # /LP2 landing
    # LP4 landing
    ("POST", "/api/v1/admin/landing/assistant"): "manage",
    ("DELETE", "/api/v1/admin/landing/assistant"): "manage",
    # /LP4 landing
    # BIL1 providers
    ("GET", "/api/v1/admin/billing/providers"): "manage",
    ("PUT", "/api/v1/admin/billing/providers/{provider}"): "manage",
    ("POST", "/api/v1/admin/billing/providers/{provider}/refresh"): "manage",
    # /BIL1 providers
    # BIL2 billing
    ("GET", "/api/v1/admin/billing/plans/{plan_id}/pricing"): "manage",
    ("PUT", "/api/v1/admin/billing/plans/{plan_id}/pricing"): "manage",
    ("GET", "/api/v1/admin/billing/plans/{plan_id}/pricing/history"): "manage",
    # TV3 price sheet
    ("GET", "/api/v1/admin/price-sheet"): "manage",
    ("POST", "/api/v1/admin/price-sheet/link"): "manage",
    ("DELETE", "/api/v1/admin/price-sheet/link"): "manage",
    # /TV3 price sheet
    # TV3 support
    ("GET", "/api/v1/support/info"): "read",
    ("GET", "/api/v1/support/requests"): "read",
    ("POST", "/api/v1/support/requests"): "write",
    ("GET", "/api/v1/support/requests/{request_id}"): "read",
    ("POST", "/api/v1/support/requests/{request_id}/messages"): "write",
    ("POST", "/api/v1/support/requests/{request_id}/close"): "write",
    ("GET", "/api/v1/admin/support/requests"): "manage",
    ("GET", "/api/v1/admin/support/requests/{request_id}"): "manage",
    ("POST", "/api/v1/admin/support/requests/{request_id}/messages"): "manage",
    ("PATCH", "/api/v1/admin/support/requests/{request_id}"): "manage",
    ("GET", "/api/v1/admin/support/stats"): "manage",
    # /TV3 support
    # TV6 whatsapp: the number and its settings, then the chats. Reading chats and answering
    # them is the team's; a chat shows a phone number
    ("GET", "/api/v1/corpora/{corpus_id}/whatsapp"): "read",
    ("POST", "/api/v1/corpora/{corpus_id}/whatsapp/embedded-signup"): "manage",
    ("PUT", "/api/v1/corpora/{corpus_id}/whatsapp/manual"): "manage",
    ("PATCH", "/api/v1/corpora/{corpus_id}/whatsapp"): "write",
    ("PUT", "/api/v1/corpora/{corpus_id}/whatsapp/documents"): "write",
    ("DELETE", "/api/v1/corpora/{corpus_id}/whatsapp"): "delete",
    ("POST", "/api/v1/corpora/{corpus_id}/whatsapp/check"): "write",
    ("GET", "/api/v1/admin/whatsapp/connections"): "manage",
    ("GET", "/api/v1/corpora/{corpus_id}/whatsapp/threads"): "read",
    ("GET", "/api/v1/corpora/{corpus_id}/whatsapp/threads/{thread_id}"): "write",
    ("POST", "/api/v1/corpora/{corpus_id}/whatsapp/threads/{thread_id}/reply"): "write",
    ("PATCH", "/api/v1/corpora/{corpus_id}/whatsapp/threads/{thread_id}"): "write",
    # /TV6 whatsapp
    # TV3 spend cap
    ("PUT", "/api/v1/billing/spend-cap"): "write",
    # /TV3 spend cap
    # TV3 notice
    ("POST", "/api/v1/admin/billing/plans/{plan_id}/pricing/preview"): "manage",
    ("DELETE", "/api/v1/admin/billing/plans/{plan_id}/pricing/scheduled"): "manage",
    ("DELETE", "/api/v1/admin/billing/accounts/{tenant_id}/pending-change"): "manage",
    # /TV3 notice
    ("GET", "/api/v1/admin/billing/accounts"): "manage",
    ("GET", "/api/v1/admin/billing/accounts/{tenant_id}"): "manage",
    ("PATCH", "/api/v1/admin/billing/accounts/{tenant_id}"): "manage",
    ("GET", "/api/v1/billing/account"): "read",
    # /BIL2 billing
    # P1 payments
    ("GET", "/api/v1/admin/company"): "manage",
    ("PUT", "/api/v1/admin/company"): "manage",
    ("GET", "/api/v1/admin/billing/tax-profile"): "manage",
    ("PUT", "/api/v1/admin/billing/tax-profile"): "manage",
    ("GET", "/api/v1/billing/tax-details"): "read",
    ("PUT", "/api/v1/billing/tax-details"): "write",
    ("GET", "/api/v1/billing/payments/config"): "read",
    ("POST", "/api/v1/billing/payments/quote"): "read",
    ("POST", "/api/v1/billing/payments/orders"): "write",
    ("POST", "/api/v1/billing/payments/orders/{order_id}/verify"): "write",
    ("GET", "/api/v1/admin/billing/payments/settings"): "manage",
    ("PUT", "/api/v1/admin/billing/payments/settings"): "manage",
    ("POST", "/api/v1/admin/billing/payments/settings/test"): "manage",
    ("GET", "/api/v1/admin/billing/payments"): "manage",
    ("GET", "/api/v1/admin/billing/payments/events"): "manage",
    ("POST", "/api/v1/admin/billing/payments/events/{event_id}/retry"): "manage",
    ("GET", "/api/v1/admin/billing/payments/{payment_id}"): "manage",
    ("POST", "/api/v1/admin/billing/payments/{payment_id}/refunds"): "manage",
    ("POST", "/api/v1/admin/billing/accounts/{tenant_id}/payment-links"): "manage",
    ("GET", "/api/v1/billing/payments"): "read",
    # /P1 payments
    # P2 invoices
    ("GET", "/api/v1/billing/invoices"): "read",
    ("GET", "/api/v1/billing/invoices/{document_id}/pdf"): "read",
    ("GET", "/api/v1/admin/billing/invoices"): "manage",
    ("GET", "/api/v1/admin/billing/invoices/export"): "manage",
    ("GET", "/api/v1/admin/billing/invoices/{document_id}/pdf"): "manage",
    ("POST", "/api/v1/admin/billing/invoices/{document_id}/resend"): "manage",
    # /P2 invoices
    # P5 plans
    ("GET", "/api/v1/billing/add-ons"): "read",
    ("POST", "/api/v1/billing/add-ons/{add_on_id}/buy"): "write",
    ("POST", "/api/v1/billing/add-ons/grants/{grant_id}/cancel"): "write",
    ("GET", "/api/v1/billing/service-orders"): "read",
    ("GET", "/api/v1/admin/billing/service-orders"): "manage",
    # P6 plan choice
    ("GET", "/api/v1/billing/plans"): "read",
    ("POST", "/api/v1/billing/plan"): "write",
    ("DELETE", "/api/v1/billing/plan/pending"): "write",
    # /P6 plan choice
    ("PATCH", "/api/v1/admin/billing/service-orders/{order_id}"): "manage",
    # /P5 plans
    # BIL3 ledger
    ("GET", "/api/v1/admin/billing/accounts/{tenant_id}/ledger"): "manage",
    ("POST", "/api/v1/admin/billing/accounts/{tenant_id}/ledger"): "manage",
    ("GET", "/api/v1/billing/ledger"): "read",
    ("GET", "/api/v1/admin/billing/reconciliation"): "manage",
    ("POST", "/api/v1/admin/billing/reconciliation"): "manage",
    # /BIL3 ledger
    # BIL6 statements
    ("GET", "/api/v1/admin/billing/accounts/{tenant_id}/statements"): "manage",
    ("GET", "/api/v1/admin/billing/accounts/{tenant_id}/statements/{month}"): "manage",
    ("GET", "/api/v1/billing/statements"): "read",
    ("GET", "/api/v1/billing/statements/{month}"): "read",
    ("GET", "/api/v1/admin/billing/pnl"): "manage",
    ("POST", "/api/v1/billing/top-up-requests"): "write",
    # /BIL6 statements
    # BIL5 add-ons
    ("GET", "/api/v1/admin/billing/add-ons"): "manage",
    ("POST", "/api/v1/admin/billing/add-ons"): "manage",
    ("PATCH", "/api/v1/admin/billing/add-ons/{add_on_id}"): "manage",
    ("DELETE", "/api/v1/admin/billing/add-ons/{add_on_id}"): "manage",
    ("GET", "/api/v1/admin/billing/accounts/{tenant_id}/grants"): "manage",
    ("POST", "/api/v1/admin/billing/accounts/{tenant_id}/grants"): "manage",
    ("POST", "/api/v1/admin/billing/accounts/{tenant_id}/grants/{grant_id}/revoke"): "manage",
    # /BIL5 add-ons
    ("GET", "/api/v1/admin/costs"): "manage",
    ("GET", "/api/v1/admin/costs/export"): "manage",
    ("GET", "/api/v1/admin/config/retrieval"): "manage",
    ("PUT", "/api/v1/admin/config/retrieval"): "manage",
    ("GET", "/api/v1/admin/eval/runs"): "manage",
    ("GET", "/api/v1/admin/eval/runs/{run_id}"): "manage",
    ("POST", "/api/v1/admin/eval/trigger"): "manage",
    ("GET", "/api/v1/admin/gaps"): "manage",
    ("POST", "/api/v1/admin/gaps/trigger"): "manage",
    ("POST", "/api/v1/admin/migrations/approve-all"): "manage",
    ("POST", "/api/v1/admin/operator-workspace/restore"): "manage",
    ("GET", "/api/v1/admin/optimize/runs"): "manage",
    ("GET", "/api/v1/admin/optimize/runs/{run_id}"): "manage",
    ("POST", "/api/v1/admin/optimize/trigger"): "manage",
    ("GET", "/api/v1/admin/plans"): "manage",
    ("POST", "/api/v1/admin/plans"): "manage",
    ("DELETE", "/api/v1/admin/plans/{plan_id}"): "manage",
    ("GET", "/api/v1/admin/plans/{plan_id}"): "manage",
    ("PATCH", "/api/v1/admin/plans/{plan_id}"): "manage",
    # PIP1 profile: pipeline profiles (how a business's questions are answered)
    ("GET", "/api/v1/admin/pipelines"): "read",
    ("POST", "/api/v1/admin/pipelines"): "manage",
    ("GET", "/api/v1/admin/pipelines/lanes"): "read",
    ("GET", "/api/v1/admin/pipelines/tenants/{tenant_id}"): "read",
    ("PUT", "/api/v1/admin/pipelines/tenants/{tenant_id}"): "manage",
    ("GET", "/api/v1/admin/pipelines/{profile_id}"): "read",
    ("PATCH", "/api/v1/admin/pipelines/{profile_id}"): "manage",
    ("DELETE", "/api/v1/admin/pipelines/{profile_id}"): "manage",
    ("POST", "/api/v1/admin/pipelines/{profile_id}/clone"): "manage",
    ("POST", "/api/v1/admin/pipelines/{profile_id}/default"): "manage",
    # /PIP1 profile
    # PIP2 local: the free local model (deployer only)
    ("GET", "/api/v1/admin/local-llm"): "read",
    ("POST", "/api/v1/admin/local-llm/test"): "manage",
    # /PIP2 local
    # L5a demo
    ("GET", "/api/v1/admin/demo"): "manage",
    ("POST", "/api/v1/admin/demo/reset"): "manage",
    # /L5a demo
    # PIP3 fair use: the deployer's view and overrides; an owner's read-only view
    ("GET", "/api/v1/admin/fair-use"): "read",
    ("GET", "/api/v1/admin/fair-use/tenants/{tenant_id}"): "read",
    ("PUT", "/api/v1/admin/fair-use/tenants/{tenant_id}"): "manage",
    ("GET", "/api/v1/fair-use"): "read",
    # /PIP3 fair use
    # COB routes are session-only; the rows exist because every non-public route is
    # classified (keys are refused before this matters).
    ("GET", "/api/v1/admin/cob/status"): "manage",
    ("POST", "/api/v1/admin/cob/resync"): "manage",
    ("GET", "/api/v1/admin/cob/review"): "manage",  # COB7
    ("GET", "/api/v1/admin/cob/documents"): "manage",  # COB9
    ("PATCH", "/api/v1/admin/cob/documents/{document_id}"): "manage",  # COB9
    # COB2: the chat routes are session-only too (people, not keys).
    ("POST", "/api/v1/cob/chat"): "read",
    ("GET", "/api/v1/cob/conversations"): "read",
    ("GET", "/api/v1/cob/conversations/{conversation_id}"): "read",
    ("DELETE", "/api/v1/cob/conversations/{conversation_id}"): "delete",
    ("POST", "/api/v1/cob/messages/{message_id}/feedback"): "write",
    ("GET", "/api/v1/cob/starters"): "read",
    # COB5: confirming or dismissing an action card (the card runs a real route with the
    # caller's own permissions, so the route itself decides what is allowed).
    ("POST", "/api/v1/cob/actions/{proposal_id}/confirm"): "write",
    ("POST", "/api/v1/cob/actions/{proposal_id}/cancel"): "write",
    # COB8: the in-app Docs (session-only too).
    ("GET", "/api/v1/docs/tree"): "read",
    ("GET", "/api/v1/docs/pages/{slug}"): "read",
    ("GET", "/api/v1/docs/search"): "read",
    ("GET", "/api/v1/admin/settings"): "manage",
    ("PATCH", "/api/v1/admin/settings"): "manage",
    ("GET", "/api/v1/admin/smtp"): "manage",
    ("POST", "/api/v1/admin/smtp/test"): "manage",
    ("GET", "/api/v1/admin/tenants"): "manage",
    ("POST", "/api/v1/admin/tenants"): "manage",
    ("DELETE", "/api/v1/admin/tenants/{tenant_id}"): "manage",
    ("GET", "/api/v1/admin/tenants/{tenant_id}"): "manage",
    ("PUT", "/api/v1/admin/tenants/{tenant_id}"): "manage",
    ("POST", "/api/v1/admin/tenants/{tenant_id}/activate"): "manage",
    ("POST", "/api/v1/admin/tenants/{tenant_id}/decline"): "manage",
    ("GET", "/api/v1/admin/tenants/{tenant_id}/delete-preview"): "manage",
    ("POST", "/api/v1/admin/tenants/{tenant_id}/invitations"): "manage",
    ("DELETE", "/api/v1/admin/tenants/{tenant_id}/invitations/{invitation_id}"): "manage",
    ("POST", "/api/v1/admin/tenants/{tenant_id}/invitations/{invitation_id}/resend"): "manage",
    ("GET", "/api/v1/admin/tenants/{tenant_id}/keys"): "manage",
    ("POST", "/api/v1/admin/tenants/{tenant_id}/keys"): "manage",
    ("DELETE", "/api/v1/admin/tenants/{tenant_id}/keys/{key_id}"): "manage",
    ("PATCH", "/api/v1/admin/tenants/{tenant_id}/keys/{key_id}"): "manage",
    ("POST", "/api/v1/admin/tenants/{tenant_id}/keys/{key_id}/rotate"): "manage",
    ("GET", "/api/v1/admin/tenants/{tenant_id}/members"): "manage",
    ("DELETE", "/api/v1/admin/tenants/{tenant_id}/members/{user_id}"): "manage",
    ("PATCH", "/api/v1/admin/tenants/{tenant_id}/members/{user_id}"): "manage",
    ("GET", "/api/v1/admin/tenants/{tenant_id}/plan"): "manage",
    ("POST", "/api/v1/admin/tenants/{tenant_id}/plan"): "manage",
    ("POST", "/api/v1/admin/tenants/{tenant_id}/reactivate"): "manage",
    ("POST", "/api/v1/admin/tenants/{tenant_id}/rotate-key"): "manage",
    ("POST", "/api/v1/admin/tenants/{tenant_id}/suspend"): "manage",
    ("GET", "/api/v1/analytics/gaps"): "read",
    ("POST", "/api/v1/analytics/gaps/run"): "write",
    ("GET", "/api/v1/analytics/gaps/{report_id}"): "read",
    ("GET", "/api/v1/analytics/queries/confidence"): "read",
    ("GET", "/api/v1/analytics/queries/count"): "read",
    ("GET", "/api/v1/analytics/queries/history"): "read",
    ("GET", "/api/v1/analytics/queries/trends"): "read",
    ("GET", "/api/v1/assistant/analytics"): "read",
    ("GET", "/api/v1/assistant/templates"): "read",
    ("GET", "/api/v1/assistant/templates/{key}"): "read",
    ("POST", "/api/v1/auth/change-password"): "manage",
    ("GET", "/api/v1/auth/me"): "manage",
    ("PATCH", "/api/v1/auth/me"): "manage",
    ("GET", "/api/v1/auth/sessions"): "manage",
    ("POST", "/api/v1/auth/sessions/revoke-others"): "manage",
    ("DELETE", "/api/v1/auth/sessions/{session_id}"): "manage",
    ("POST", "/api/v1/auth/switch-admin"): "manage",
    ("POST", "/api/v1/auth/switch-tenant"): "manage",
    ("GET", "/api/v1/config"): "read",
    ("PATCH", "/api/v1/config"): "write",
    ("GET", "/api/v1/corpora"): "read",
    ("POST", "/api/v1/corpora"): "write",
    ("DELETE", "/api/v1/corpora/{corpus_id}"): "delete",
    ("GET", "/api/v1/corpora/{corpus_id}"): "read",
    ("PUT", "/api/v1/corpora/{corpus_id}"): "write",
    ("GET", "/api/v1/corpora/{corpus_id}/assistant"): "read",
    # A pure preview: no model call, nothing is written.
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/compile"): "read",
    # Spends the knowledge base's model, so a read key may not (compile makes no model call).
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/rules/improve"): "write",
    # Discarding a draft removes unpublished work that PUT draft can already overwrite; no
    # live behaviour, no history. Publishing is reversible by a restore, so it is `write` too
    # (a member's session still cannot publish: `require_owner`).
    ("DELETE", "/api/v1/corpora/{corpus_id}/assistant/draft"): "write",
    ("PATCH", "/api/v1/corpora/{corpus_id}/assistant/draft"): "write",
    ("PUT", "/api/v1/corpora/{corpus_id}/assistant/draft"): "write",
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/draft/from-template"): "write",
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/enabled"): "write",
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/publish"): "write",
    ("GET", "/api/v1/corpora/{corpus_id}/assistant/analytics"): "read",
    ("GET", "/api/v1/corpora/{corpus_id}/assistant/review"): "read",
    # Writes the assistant's draft (never the published version) and an action row.
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/review/actions"): "write",
    ("GET", "/api/v1/corpora/{corpus_id}/assistant/versions"): "read",
    ("GET", "/api/v1/corpora/{corpus_id}/assistant/versions/{version}"): "read",
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/versions/{version}/restore"): "write",
    # Guided setup (C8). propose spends model tokens; generate saves the draft; tests creates a
    # golden set and a run; apply publishes, at the same level as assistant/publish.
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/setup"): "write",
    ("GET", "/api/v1/corpora/{corpus_id}/assistant/setup/{sid}"): "read",
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/setup/{sid}/propose"): "write",
    ("GET", "/api/v1/corpora/{corpus_id}/assistant/setup/{sid}/questions"): "read",
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/setup/{sid}/answers"): "write",
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/setup/{sid}/generate"): "write",
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/setup/{sid}/tests"): "write",
    ("GET", "/api/v1/corpora/{corpus_id}/assistant/setup/{sid}/gaps"): "read",
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/setup/{sid}/apply"): "write",
    ("GET", "/api/v1/corpora/{corpus_id}/conversations"): "read",
    ("POST", "/api/v1/corpora/{corpus_id}/conversations"): "read",
    ("DELETE", "/api/v1/corpora/{corpus_id}/conversations/{conversation_id}"): "delete",
    ("GET", "/api/v1/corpora/{corpus_id}/conversations/{conversation_id}"): "read",
    ("PATCH", "/api/v1/corpora/{corpus_id}/conversations/{conversation_id}"): "write",
    ("GET", "/api/v1/corpora/{corpus_id}/conversations/{conversation_id}/messages"): "read",
    ("POST", "/api/v1/corpora/{corpus_id}/conversations/{conversation_id}/messages"): "read",
    ("GET", "/api/v1/corpora/{corpus_id}/delete-preview"): "read",
    ("GET", "/api/v1/corpora/{corpus_id}/eval/criteria"): "read",
    ("POST", "/api/v1/corpora/{corpus_id}/eval/criteria"): "write",
    # A recreatable config row, so `write` rather than `delete`.
    ("DELETE", "/api/v1/corpora/{corpus_id}/eval/criteria/{criterion_id}"): "write",
    ("PATCH", "/api/v1/corpora/{corpus_id}/eval/criteria/{criterion_id}"): "write",
    # Pure computation over runs the caller can already read.
    ("POST", "/api/v1/corpora/{corpus_id}/eval/compare"): "read",
    # No write, no model call: an estimate from the price table.
    ("POST", "/api/v1/corpora/{corpus_id}/eval/estimate"): "read",
    ("GET", "/api/v1/corpora/{corpus_id}/eval/runs"): "read",
    # Spends model money and monthly questions, so a `read` key cannot start one.
    ("POST", "/api/v1/corpora/{corpus_id}/eval/runs"): "write",
    ("GET", "/api/v1/corpora/{corpus_id}/eval/runs/{run_id}"): "read",
    ("GET", "/api/v1/corpora/{corpus_id}/eval/runs/{run_id}/samples"): "read",
    ("PATCH", "/api/v1/corpora/{corpus_id}/eval/samples/{sample_id}"): "write",
    ("GET", "/api/v1/corpora/{corpus_id}/eval/settings"): "read",
    # Holds a judge key: `write` plus `require_owner` (like `llm_config` on PUT /corpora/{id}).
    ("PUT", "/api/v1/corpora/{corpus_id}/eval/settings"): "write",
    ("GET", "/api/v1/corpora/{corpus_id}/golden-sets"): "read",
    ("POST", "/api/v1/corpora/{corpus_id}/golden-sets"): "write",
    ("POST", "/api/v1/corpora/{corpus_id}/golden-sets/from-template"): "write",
    # Like DELETE /api/v1/golden/{pair_id}.
    ("DELETE", "/api/v1/corpora/{corpus_id}/golden-sets/{set_id}"): "delete",
    ("GET", "/api/v1/corpora/{corpus_id}/golden-sets/{set_id}"): "read",
    ("PATCH", "/api/v1/corpora/{corpus_id}/golden-sets/{set_id}"): "write",
    ("POST", "/api/v1/corpora/{corpus_id}/golden-sets/{set_id}/pairs"): "write",
    ("GET", "/api/v1/corpora/{corpus_id}/keys"): "manage",
    ("POST", "/api/v1/corpora/{corpus_id}/keys"): "manage",
    ("DELETE", "/api/v1/corpora/{corpus_id}/keys/{key_id}"): "manage",
    ("PATCH", "/api/v1/corpora/{corpus_id}/keys/{key_id}"): "manage",
    ("POST", "/api/v1/corpora/{corpus_id}/keys/{key_id}/rotate"): "manage",
    ("DELETE", "/api/v1/corpora/{corpus_id}/memories"): "delete",
    ("GET", "/api/v1/corpora/{corpus_id}/memories"): "read",
    ("POST", "/api/v1/corpora/{corpus_id}/memories"): "write",
    ("POST", "/api/v1/corpora/{corpus_id}/memories/bulk"): "write",
    ("DELETE", "/api/v1/corpora/{corpus_id}/memories/purge"): "delete",
    ("POST", "/api/v1/corpora/{corpus_id}/memories/search"): "read",
    ("DELETE", "/api/v1/corpora/{corpus_id}/memories/{memory_id}"): "delete",
    ("GET", "/api/v1/corpora/{corpus_id}/memories/{memory_id}"): "read",
    ("GET", "/api/v1/corpora/{corpus_id}/migrations"): "read",
    ("POST", "/api/v1/corpora/{corpus_id}/rotate-key"): "manage",
    # Leads are contact data: reading them needs a key allowed to change things, not the
    # read-only agent default (conversation C4).
    ("GET", "/api/v1/leads"): "write",
    ("GET", "/api/v1/leads/export.csv"): "write",
    ("DELETE", "/api/v1/leads/{lead_id}"): "delete",
    ("GET", "/api/v1/leads/{lead_id}"): "write",
    ("PATCH", "/api/v1/leads/{lead_id}"): "write",
    # Knowledge-base webhooks receive every future lead's contact data (conversation C4).
    ("GET", "/api/v1/corpora/{corpus_id}/webhooks"): "manage",
    ("POST", "/api/v1/corpora/{corpus_id}/webhooks"): "manage",
    ("DELETE", "/api/v1/corpora/{corpus_id}/webhooks/{webhook_id}"): "manage",
    ("PATCH", "/api/v1/corpora/{corpus_id}/webhooks/{webhook_id}"): "manage",
    ("GET", "/api/v1/corpora/{corpus_id}/widget-keys"): "read",
    ("POST", "/api/v1/corpora/{corpus_id}/widget-keys"): "write",
    ("DELETE", "/api/v1/corpora/{corpus_id}/widget-keys/{key_id}"): "delete",
    ("PATCH", "/api/v1/corpora/{corpus_id}/widget-keys/{key_id}"): "write",
    ("POST", "/api/v1/corpora/{corpus_id}/widget-keys/{key_id}/rotate"): "delete",
    ("GET", "/api/v1/documents"): "read",
    ("DELETE", "/api/v1/documents/{document_id}"): "delete",
    ("GET", "/api/v1/documents/{document_id}"): "read",
    ("GET", "/api/v1/documents/{document_id}/delete-preview"): "read",
    ("GET", "/api/v1/documents/{document_id}/versions"): "read",
    ("POST", "/api/v1/documents/{document_id}/versions/{version_id}/restore"): "write",
    ("POST", "/api/v1/documents/{new_document_id}/replace/{old_document_id}"): "write",
    ("GET", "/api/v1/feedback"): "read",
    ("POST", "/api/v1/feedback"): "write",
    ("GET", "/api/v1/feedback/stats"): "read",
    ("GET", "/api/v1/golden"): "read",
    ("POST", "/api/v1/golden"): "write",
    ("GET", "/api/v1/golden/export"): "read",
    ("POST", "/api/v1/golden/from-feedback"): "write",
    ("POST", "/api/v1/golden/import"): "write",
    ("DELETE", "/api/v1/golden/{pair_id}"): "delete",
    ("PATCH", "/api/v1/golden/{pair_id}"): "write",
    ("GET", "/api/v1/handoffs"): "read",
    # TV2 reference: ids only, no contact data
    ("GET", "/api/v1/handoffs/lookup"): "read",
    # /TV2 reference
    # The detail carries a callback request's phone and e-mail: not for a read key.
    ("GET", "/api/v1/handoffs/{handoff_id}"): "write",
    ("PATCH", "/api/v1/handoffs/{handoff_id}"): "write",
    ("GET", "/api/v1/info"): "read",
    ("POST", "/api/v1/ingest"): "write",
    ("GET", "/api/v1/ingest/jobs"): "read",
    ("GET", "/api/v1/ingest/jobs/{job_id}"): "read",
    # COB5: re-run a failed upload from its stored file
    ("POST", "/api/v1/ingest/jobs/{job_id}/retry"): "write",
    ("POST", "/api/v1/ingest/records"): "write",
    ("POST", "/api/v1/ingest/{job_id}/confirm"): "write",
    ("GET", "/api/v1/migrations"): "read",
    ("GET", "/api/v1/migrations/{migration_id}"): "read",
    ("POST", "/api/v1/migrations/{migration_id}/approve"): "write",
    ("POST", "/api/v1/migrations/{migration_id}/cancel"): "write",
    ("POST", "/api/v1/migrations/{migration_id}/retry"): "write",
    ("POST", "/api/v1/migrations/{migration_id}/rollback"): "delete",
    ("GET", "/api/v1/models"): "read",
    ("GET", "/api/v1/models/answer-benchmarks"): "read",
    ("GET", "/api/v1/models/benchmark-summary"): "read",
    ("GET", "/api/v1/models/fast-benchmarks"): "read",
    ("POST", "/api/v1/models/discover/openrouter"): "read",
    ("GET", "/api/v1/models/embeddings"): "read",
    ("GET", "/api/v1/models/presets"): "read",
    ("GET", "/api/v1/portal/corpus/{corpus_id}/rules"): "read",
    ("POST", "/api/v1/portal/corpus/{corpus_id}/rules"): "write",
    ("DELETE", "/api/v1/portal/corpus/{corpus_id}/rules/{rule_id}"): "delete",
    ("PATCH", "/api/v1/portal/corpus/{corpus_id}/rules/{rule_id}"): "write",
    ("GET", "/api/v1/portal/workspaces"): "read",
    ("POST", "/api/v1/portal/workspaces"): "write",
    ("POST", "/api/v1/portal/workspaces/from-ingestion"): "write",
    ("DELETE", "/api/v1/portal/workspaces/{workspace_id}"): "delete",
    ("GET", "/api/v1/portal/workspaces/{workspace_id}"): "read",
    ("PATCH", "/api/v1/portal/workspaces/{workspace_id}"): "write",
    ("POST", "/api/v1/portal/workspaces/{workspace_id}/approve"): "write",
    ("GET", "/api/v1/portal/workspaces/{workspace_id}/audit-trail"): "read",
    ("POST", "/api/v1/portal/workspaces/{workspace_id}/chat"): "write",
    ("POST", "/api/v1/portal/workspaces/{workspace_id}/chat/execute"): "write",
    ("GET", "/api/v1/portal/workspaces/{workspace_id}/chat/history"): "read",
    ("GET", "/api/v1/portal/workspaces/{workspace_id}/chunks"): "read",
    ("POST", "/api/v1/portal/workspaces/{workspace_id}/chunks"): "write",
    ("POST", "/api/v1/portal/workspaces/{workspace_id}/chunks/merge"): "write",
    ("POST", "/api/v1/portal/workspaces/{workspace_id}/chunks/reorder"): "write",
    ("DELETE", "/api/v1/portal/workspaces/{workspace_id}/chunks/{chunk_id}"): "delete",
    ("PATCH", "/api/v1/portal/workspaces/{workspace_id}/chunks/{chunk_id}"): "write",
    ("POST", "/api/v1/portal/workspaces/{workspace_id}/chunks/{chunk_id}/approve"): "write",
    ("POST", "/api/v1/portal/workspaces/{workspace_id}/chunks/{chunk_id}/reject"): "write",
    ("POST", "/api/v1/portal/workspaces/{workspace_id}/chunks/{chunk_id}/split"): "write",
    ("GET", "/api/v1/portal/workspaces/{workspace_id}/content"): "read",
    ("POST", "/api/v1/portal/workspaces/{workspace_id}/content"): "write",
    ("GET", "/api/v1/portal/workspaces/{workspace_id}/content/{content_id}"): "read",
    ("POST", "/api/v1/portal/workspaces/{workspace_id}/content/{content_id}/reprocess"): "write",
    ("POST", "/api/v1/portal/workspaces/{workspace_id}/finalize-review"): "write",
    ("POST", "/api/v1/portal/workspaces/{workspace_id}/ingest"): "write",
    ("POST", "/api/v1/portal/workspaces/{workspace_id}/lock"): "write",
    ("POST", "/api/v1/portal/workspaces/{workspace_id}/reject"): "write",
    ("GET", "/api/v1/portal/workspaces/{workspace_id}/review-status"): "read",
    ("POST", "/api/v1/portal/workspaces/{workspace_id}/submit"): "write",
    ("POST", "/api/v1/portal/workspaces/{workspace_id}/unlock"): "write",
    ("POST", "/api/v1/query"): "read",
    ("POST", "/api/v1/query/retrieve"): "read",
    ("POST", "/api/v1/query/stream"): "read",
    ("GET", "/api/v1/query/{query_id}"): "read",
    ("GET", "/api/v1/tenants/me"): "read",
    ("DELETE", "/api/v1/tenants/me/invitations/{invitation_id}"): "manage",
    ("POST", "/api/v1/tenants/me/invitations/{invitation_id}/resend"): "manage",
    ("GET", "/api/v1/tenants/me/keys"): "manage",
    ("POST", "/api/v1/tenants/me/keys"): "manage",
    ("DELETE", "/api/v1/tenants/me/keys/{key_id}"): "manage",
    ("PATCH", "/api/v1/tenants/me/keys/{key_id}"): "manage",
    ("POST", "/api/v1/tenants/me/keys/{key_id}/rotate"): "manage",
    ("GET", "/api/v1/tenants/me/members"): "manage",
    ("POST", "/api/v1/tenants/me/members"): "manage",
    ("DELETE", "/api/v1/tenants/me/members/{user_id}"): "manage",
    ("PATCH", "/api/v1/tenants/me/members/{user_id}"): "manage",
    ("GET", "/api/v1/tenants/me/plan"): "read",
    ("POST", "/api/v1/tenants/me/rotate-key"): "manage",
    ("GET", "/api/v1/usage"): "read",
    ("GET", "/api/v1/usage/consumption"): "read",
    ("GET", "/api/v1/webhooks"): "read",
    ("POST", "/api/v1/webhooks"): "write",
    ("DELETE", "/api/v1/webhooks/{webhook_id}"): "delete",
    ("PATCH", "/api/v1/webhooks/{webhook_id}"): "write",
    ("POST", "/api/v1/webhooks/{webhook_id}/test"): "write",
    ("GET", "/api/v1/whoami"): "read",
    # BIZ3 simple mode
    ("POST", "/api/v1/corpora/{corpus_id}/articles"): "write",
    ("GET", "/api/v1/corpora/{corpus_id}/articles/{document_id}"): "read",
    ("PUT", "/api/v1/corpora/{corpus_id}/articles/{document_id}"): "write",
    ("GET", "/api/v1/home"): "read",
    # /BIZ3 simple mode
    # TV4 value
    ("GET", "/api/v1/value/settings"): "read",
    ("PUT", "/api/v1/value/settings"): "write",
    ("GET", "/api/v1/value/report"): "read",
    ("GET", "/api/v1/value/reports"): "read",
    ("GET", "/api/v1/admin/tenants/{tenant_id}/value-report"): "manage",
    # /TV4 value
    # BIZ1 learning
    ("GET", "/api/v1/corpora/{corpus_id}/website"): "read",
    ("PATCH", "/api/v1/corpora/{corpus_id}/website"): "write",
    ("POST", "/api/v1/corpora/{corpus_id}/website/learn"): "write",
    ("POST", "/api/v1/corpora/{corpus_id}/website/reread"): "write",
    ("DELETE", "/api/v1/corpora/{corpus_id}/website/sources/{source_id}"): "delete",
    ("GET", "/api/v1/corpora/{corpus_id}/website/pages"): "read",
    ("POST", "/api/v1/corpora/{corpus_id}/website/pages/{page_id}/reread"): "write",
    ("PATCH", "/api/v1/corpora/{corpus_id}/website/pages/{page_id}"): "write",
    # /BIZ1 learning
    # BIZ2 freshness
    ("POST", "/api/v1/corpora/{corpus_id}/website/learn-key"): "manage",
    ("DELETE", "/api/v1/corpora/{corpus_id}/website/learn-key"): "manage",
    # /BIZ2 freshness
    # BIZ4 onboarding
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/avatar"): "write",
    ("GET", "/api/v1/onboarding"): "read",
    ("POST", "/api/v1/onboarding/website"): "write",
    ("PATCH", "/api/v1/onboarding"): "write",
    ("GET", "/api/v1/corpora/{corpus_id}/website/suggestions"): "read",
    # /BIZ4 onboarding
}


def _pattern(template: str) -> re.Pattern[str]:
    parts = []
    for segment in template.split("/"):
        # COB8: a Docs page slug ("screens/widget") holds slashes; OpenAPI names it `{slug}`.
        if (segment == "{slug}" and template.endswith("/docs/pages/{slug}")) or (
            segment.startswith("{") and segment.endswith(":path}")
        ):
            parts.append(".+")
        elif segment.startswith("{") and segment.endswith("}"):
            parts.append("[^/]+")
        else:
            parts.append(re.escape(segment))
    return re.compile("^" + "/".join(parts) + "$")


def _specificity(template: str) -> tuple[int, int]:
    """Fewest parameters first, so `/x/stats` beats `/x/{id}`; then the longest template."""
    return (template.count("{"), -len(template))


_MATCHERS: Final[dict[str, list[tuple[re.Pattern[str], Operation]]]] = {}
for _method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
    _entries = sorted(
        ((tpl, op) for (m, tpl), op in ROUTE_OPS.items() if m == _method),
        key=lambda e: _specificity(e[0]),
    )
    _MATCHERS[_method] = [(_pattern(tpl), op) for tpl, op in _entries]


def required_operation(method: str, path: str) -> Operation:
    """The operation `method path` is classified as; `manage` when no row matches."""
    method = "GET" if method.upper() == "HEAD" else method.upper()
    path = path.rstrip("/") or "/"
    for pattern, operation in _MATCHERS.get(method, ()):
        if pattern.match(path):
            return operation
    return "manage"


def needed_permission(operation: str) -> Permission:
    """The weakest permission that allows `operation`."""
    return _NEEDS[operation]


def permission_allows(permission: str, operation: str) -> bool:
    return operation in _ALLOWS.get(permission, frozenset())


def check_key_permission(permission: str, method: str, path: str) -> Permission | None:
    """None when a key with `permission` may call `method path`; else the permission it needs."""
    operation = required_operation(method, path)
    if permission_allows(permission, operation):
        return None
    return needed_permission(operation)


# L5a demo
def match_template(
    table: Iterable[tuple[str, str]], method: str, path: str
) -> tuple[str, str] | None:
    """The `(method, template)` row of `table` that `method path` matches, the most specific
    first (the same `_pattern`/`_specificity` as `ROUTE_OPS`); `HEAD` counts as `GET`."""
    method = "GET" if method.upper() == "HEAD" else method.upper()
    path = path.rstrip("/") or "/"
    rows = sorted((row for row in table if row[0] == method), key=lambda row: _specificity(row[1]))
    for row in rows:
        if _pattern(row[1]).match(path):
            return row
    return None


# /L5a demo
