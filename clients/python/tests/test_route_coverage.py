"""Every engine operation has a method in the sync and the async client (accounts A8).

Reads the engine's committed `docs/openapi.json` and the client's source, and finds each
`"METHOD", "/api/v1/..."` pair the client sends (path parameters compared by position).
Left out on purpose:

- `EXCLUDED_PREFIXES`: dashboard sign-in routes (`/api/v1/auth/*`, `/api/v1/invitations/*`;
  the SDKs stay key-based), the Content Portal (`/api/v1/portal/*`, never wrapped) and the
  widget's own routes (`/api/v1/widget/*`: `widget.js` calls them with a publishable key).
- `NOT_WRAPPED`: operations that existed before the accounts layer and that this SDK has
  never wrapped (the widget's own routes are for `widget.js`). The list only shrinks: a NEW
  engine operation missing from the client fails here, and so does an entry that the
  client now wraps or that the engine dropped.
"""

import json
import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SPEC = HERE.parents[2] / "docs" / "openapi.json"
SOURCE = (HERE.parent / "cob_rag_client" / "client.py").read_text(encoding="utf-8")
SYNC_SOURCE, ASYNC_SOURCE = SOURCE.split("\nclass AsyncCobRag", 1)

EXCLUDED_PREFIXES = (
    "/api/v1/admin/cob/",  # COB: session-only, not for API-key clients
    "/api/v1/auth/",
    "/api/v1/cob/",  # COB chat (COB2): people, not keys
    "/api/v1/docs/",  # the in-app Docs (COB8): session-only
    "/api/v1/invitations/",
    "/api/v1/portal/",
    "/api/v1/public/cob/",  # the public COB (COB6): browsers
    "/api/v1/widget/",
)

NOT_WRAPPED = {
    "GET /api/v1/admin/demo",  # L5a: the operator's demo status (dashboard, people not keys)
    "POST /api/v1/admin/demo/reset",  # L5a: the operator's demo reset button
    "GET /api/v1/public/whatsapp/webhook",  # TV6: Meta's webhook verification (Meta, not a client)
    "POST /api/v1/public/whatsapp/webhook",  # TV6: Meta's webhook deliveries
    "POST /api/v1/corpora/{}/whatsapp/embedded-signup",  # TV6: the dashboard's Meta sign-up window
    "POST /api/v1/corpora/{}/whatsapp/check",  # TV6: the dashboard's Check the connection button
    "GET /api/v1/admin/whatsapp/connections",  # TV6: the deployer's WhatsApp numbers list
    "GET /api/v1/support/info",  # TV3: the dashboard's Get help button
    "GET /api/v1/support/requests",  # TV3: the dashboard's Support page (people, not keys)
    "POST /api/v1/support/requests",  # TV3: ditto
    "GET /api/v1/support/requests/{}",  # TV3: ditto
    "POST /api/v1/support/requests/{}/messages",  # TV3: ditto
    "POST /api/v1/support/requests/{}/close",  # TV3: ditto
    "GET /api/v1/admin/support/requests",  # TV3: the provider's Support inbox
    "GET /api/v1/admin/support/requests/{}",  # TV3: ditto
    "PATCH /api/v1/admin/support/requests/{}",  # TV3: ditto
    "POST /api/v1/admin/support/requests/{}/messages",  # TV3: ditto
    "GET /api/v1/admin/support/stats",  # TV3: ditto
    "GET /api/v1/admin/price-sheet",  # TV3: the dashboard's Price list page
    "POST /api/v1/admin/price-sheet/link",  # TV3: ditto
    "DELETE /api/v1/admin/price-sheet/link",  # TV3: ditto
    "GET /api/v1/public/price-sheet/{}",  # TV3: a prospect's browser opens the shared list by its token
    # P3 legal
    "GET /api/v1/public/legal",  # P3: the dashboard footers and checkout read the published pages
    "GET /api/v1/public/legal/{}",  # P3: ditto, one page
    "GET /api/v1/admin/legal",  # P3: the operator's Legal pages screen
    "GET /api/v1/admin/legal/settings",  # P3: ditto
    "PUT /api/v1/admin/legal/settings",  # P3: ditto
    "GET /api/v1/admin/legal/acceptances",  # P3: ditto: who accepted which version
    "GET /api/v1/admin/legal/{}",  # P3: ditto
    "PUT /api/v1/admin/legal/{}/draft",  # P3: ditto
    "POST /api/v1/admin/legal/{}/preview",  # P3: ditto
    "POST /api/v1/admin/legal/{}/publish",  # P3: ditto
    # /P3 legal
    # LP1 landing: the operator's Public website screen (dashboard only)
    "GET /api/v1/admin/landing",
    "PUT /api/v1/admin/landing/draft",
    "PUT /api/v1/admin/landing/settings",
    "POST /api/v1/admin/landing/theme/preview",
    "POST /api/v1/admin/landing/preview-link",
    "POST /api/v1/admin/landing/publish",
    "GET /api/v1/admin/landing/versions",
    "POST /api/v1/admin/landing/versions/{}/restore",
    "POST /api/v1/admin/landing/assets",
    "DELETE /api/v1/admin/landing/assets/{}",
    # /LP1 landing
    # LP4 landing: connecting "Ask it yourself" to an assistant (dashboard only)
    "POST /api/v1/admin/landing/assistant",
    "DELETE /api/v1/admin/landing/assistant",
    # /LP4 landing
    # LP2 landing: the website editor's starting examples and "generate from my knowledge base"
    "GET /api/v1/admin/landing/presets",
    "GET /api/v1/admin/landing/presets/{}",
    "POST /api/v1/admin/landing/generate",
    # /LP2 landing
    # P1 payments
    "GET /api/v1/admin/company",  # P1: the operator's Company card
    "PUT /api/v1/admin/company",  # P1: ditto
    "GET /api/v1/admin/billing/tax-profile",  # P1: the operator's seller tax profile form
    "PUT /api/v1/admin/billing/tax-profile",  # P1: ditto
    "GET /api/v1/admin/billing/payments/settings",  # P1: the payment settings card (keys, webhook)
    "PUT /api/v1/admin/billing/payments/settings",  # P1: ditto
    "POST /api/v1/admin/billing/payments/settings/test",  # P1: ditto, Test connection
    "GET /api/v1/admin/billing/payments/events",  # P1: the operator's failed-deliveries list
    "POST /api/v1/admin/billing/payments/events/{}/retry",  # P1: ditto
    "GET /api/v1/billing/payments/config",  # P1: the dashboard's pay page
    "POST /api/v1/billing/payments/quote",  # P1: ditto
    "POST /api/v1/billing/payments/orders",  # P1: ditto
    "POST /api/v1/billing/payments/orders/{}/verify",  # P1: ditto
    "POST /api/v1/public/payments/razorpay/webhook",  # P1: the payment service's deliveries (not a client)
    # /P1 payments
    # P2 invoices
    "GET /api/v1/billing/invoices/{}/pdf",  # P2: the dashboard's Download button (a file, not JSON)
    "GET /api/v1/admin/billing/invoices/export",  # P2: the operator's CSV for the accountant
    "GET /api/v1/admin/billing/invoices/{}/pdf",  # P2: ditto, one PDF
    "POST /api/v1/admin/billing/invoices/{}/resend",  # P2: the operator's Resend button
    # /P2 invoices
    # P5 shop
    "GET /api/v1/billing/add-ons",  # P5: the dashboard shop (people, not keys)
    "POST /api/v1/billing/add-ons/{}/buy",  # P5: ditto
    "POST /api/v1/billing/add-ons/grants/{}/cancel",  # P5: ditto
    "GET /api/v1/billing/plans",  # P6: the dashboard Change plan card (people, not keys)
    "POST /api/v1/billing/plan",  # P6: ditto
    "DELETE /api/v1/billing/plan/pending",  # P6: ditto
    "GET /api/v1/public/plans",  # P6: the sign-up page in a browser
    "GET /api/v1/billing/service-orders",  # P5: the owner's service orders
    "GET /api/v1/admin/billing/service-orders",  # P5: the provider's order list
    "PATCH /api/v1/admin/billing/service-orders/{}",  # P5: the provider moves an order
    # /P5 shop
    "GET /api/v1/onboarding",  # BIZ4: the dashboard's first-run steps (people, not keys)
    "POST /api/v1/onboarding/website",  # BIZ4: ditto
    "PATCH /api/v1/onboarding",  # BIZ4: ditto
    "GET /api/v1/public/avatars/{}",  # BIZ4: a browser loads the picture, by its own address
    "GET /api/v1/corpora/{}/website/suggestions",  # BIZ4: the first-run chat's question chips
    "GET /api/v1/public/learn/{}",  # BIZ2: a website's own call; the key is its credential
    "POST /api/v1/public/learn/{}",  # BIZ2: ditto (IndexNow-shaped body)
    "POST /api/v1/public/landing/contact",  # LP3: the website's contact form (a browser, no key)
    "GET /api/v1/admin/billing/plans/{}/pricing/history",  # BIL2: the dashboard's version history
    "GET /api/v1/admin/billing/pnl",  # BIL6: the dashboard's P&L tab
    "POST /api/v1/admin/pipelines/{}/clone",  # PIP1: the dashboard's Duplicate button
    "POST /api/v1/admin/pipelines/{}/default",  # PIP1: the dashboard's Make default button
    "GET /api/v1/admin/pipelines/lanes",  # PIP1: the dashboard's live answer-lane view
    "GET /api/v1/admin/local-llm",  # PIP2: the Engine page's local model card
    "POST /api/v1/admin/local-llm/test",  # PIP2: the card's "Try it" button
    "PUT /api/v1/admin/billing/providers/{}",  # BIL1: the dashboard's provider settings
    "POST /api/v1/admin/billing/providers/{}/refresh",  # BIL1: the dashboard's Refresh button
    "GET /api/v1/admin/billing/reconciliation",  # BIL3: the dashboard's reconciliation card
    "POST /api/v1/admin/billing/reconciliation",  # BIL3: its Run now button
    "GET /api/v1/admin/costs/export",
    "GET /api/v1/admin/config/retrieval",
    "PUT /api/v1/admin/config/retrieval",
    "GET /api/v1/admin/eval/runs",
    "GET /api/v1/admin/eval/runs/{}",
    "POST /api/v1/admin/eval/trigger",
    "GET /api/v1/admin/gaps",
    "POST /api/v1/admin/gaps/trigger",
    "POST /api/v1/admin/migrations/approve-all",
    "GET /api/v1/admin/optimize/runs",
    "GET /api/v1/admin/optimize/runs/{}",
    "POST /api/v1/admin/optimize/trigger",
    "GET /api/v1/admin/tenants/{}",
    "PUT /api/v1/admin/tenants/{}",
    "GET /api/v1/analytics/gaps",
    "POST /api/v1/analytics/gaps/run",
    "GET /api/v1/analytics/gaps/{}",
    "GET /api/v1/analytics/queries/confidence",
    "GET /api/v1/analytics/queries/count",
    "GET /api/v1/analytics/queries/history",
    "GET /api/v1/analytics/queries/trends",
    "GET /api/v1/config",
    "PATCH /api/v1/config",
    "GET /api/v1/corpora/{}/conversations",
    "POST /api/v1/corpora/{}/conversations",
    "DELETE /api/v1/corpora/{}/conversations/{}",
    "GET /api/v1/corpora/{}/conversations/{}",
    "PATCH /api/v1/corpora/{}/conversations/{}",
    "GET /api/v1/corpora/{}/conversations/{}/messages",
    "POST /api/v1/corpora/{}/conversations/{}/messages",
    "DELETE /api/v1/corpora/{}/memories",
    "GET /api/v1/corpora/{}/memories",
    "POST /api/v1/corpora/{}/memories",
    "POST /api/v1/corpora/{}/memories/bulk",
    "DELETE /api/v1/corpora/{}/memories/purge",
    "POST /api/v1/corpora/{}/memories/search",
    "DELETE /api/v1/corpora/{}/memories/{}",
    "GET /api/v1/corpora/{}/memories/{}",
    "GET /api/v1/corpora/{}/migrations",
    "POST /api/v1/documents/{}/replace/{}",
    "GET /api/v1/documents/{}/versions",
    "POST /api/v1/documents/{}/versions/{}/restore",
    "GET /api/v1/feedback",
    "POST /api/v1/feedback",
    "GET /api/v1/feedback/stats",
    "GET /api/v1/golden",
    "POST /api/v1/golden",
    "GET /api/v1/golden/export",
    "POST /api/v1/golden/from-feedback",
    "POST /api/v1/golden/import",
    "DELETE /api/v1/golden/{}",
    "PATCH /api/v1/golden/{}",
    "POST /api/v1/ingest/{}/confirm",
    "GET /api/v1/migrations",
    "GET /api/v1/migrations/{}",
    "POST /api/v1/migrations/{}/approve",
    "POST /api/v1/migrations/{}/cancel",
    "POST /api/v1/migrations/{}/retry",
    "POST /api/v1/migrations/{}/rollback",
    "GET /api/v1/models",
    "POST /api/v1/models/discover/openrouter",
    "GET /api/v1/models/embeddings",
    "GET /api/v1/models/presets",
    "GET /api/v1/query/{}",
    "GET /api/v1/webhooks",
    "POST /api/v1/webhooks",
    "DELETE /api/v1/webhooks/{}",
    "PATCH /api/v1/webhooks/{}",
    "POST /api/v1/webhooks/{}/test",
}
# The async client came later and still lacks a few of the sync client's older calls.
ASYNC_ALSO_NOT_WRAPPED = {
    "POST /api/v1/admin/tenants/{}/rotate-key",
    "POST /api/v1/corpora/{}/rotate-key",
    "DELETE /api/v1/corpora/{}/widget-keys/{}",
    "PATCH /api/v1/corpora/{}/widget-keys/{}",
    "POST /api/v1/corpora/{}/widget-keys/{}/rotate",
    "POST /api/v1/tenants/me/rotate-key",
}

_PARAM = re.compile(r"\{[^}]*\}")
_CALL = re.compile(r'"(GET|POST|PUT|PATCH|DELETE)",\s*f?"(/[^"]*)"')


def _norm(path: str) -> str:
    return _PARAM.sub("{}", path.split("?")[0])


def _spec_operations(*, excluded: tuple[str, ...] = EXCLUDED_PREFIXES) -> set[str]:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    return {
        f"{method.upper()} {_norm(path)}"
        for path, item in spec["paths"].items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
        and not (excluded and path.startswith(excluded))
    }


def _client_operations(source: str) -> set[str]:
    return {f"{m} {_norm(p)}" for m, p in _CALL.findall(source)}


CLIENTS = [
    pytest.param(SYNC_SOURCE, NOT_WRAPPED, id="sync"),
    pytest.param(ASYNC_SOURCE, NOT_WRAPPED | ASYNC_ALSO_NOT_WRAPPED, id="async"),
]


@pytest.mark.parametrize(("source", "allowed_gaps"), CLIENTS)
def test_every_operation_has_a_method(source: str, allowed_gaps: set[str]) -> None:
    missing = _spec_operations() - _client_operations(source) - allowed_gaps
    assert not missing, f"no SDK method for: {sorted(missing)}"


@pytest.mark.parametrize(("source", "allowed_gaps"), CLIENTS)
def test_the_not_wrapped_list_only_shrinks(source: str, allowed_gaps: set[str]) -> None:
    stale = allowed_gaps - (_spec_operations() - _client_operations(source))
    assert not stale, f"wrapped now or gone from the engine, remove from the list: {sorted(stale)}"


def test_the_client_calls_only_real_operations() -> None:
    unknown = _client_operations(SOURCE) - _spec_operations(excluded=())
    assert not unknown, f"the client calls operations the engine does not have: {sorted(unknown)}"
