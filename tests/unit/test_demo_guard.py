import re

import pytest

from callobuzz_rag.api.permissions import PUBLIC_ROUTES, ROUTE_OPS, required_operation
from callobuzz_rag.demo import guard

ID = "00000000-0000-0000-0000-000000000001"


def _concrete(template: str) -> str:
    return re.sub(r"\{[^}]+\}", ID, template)


@pytest.mark.parametrize(
    ("method", "path", "reason"),
    [
        ("POST", "/api/v1/auth/login", None),
        ("POST", "/api/v1/auth/refresh", None),
        ("POST", "/api/v1/auth/logout", None),
        ("POST", "/api/v1/auth/signup", "account"),
        ("POST", "/api/v1/auth/forgot", "account"),
        ("POST", "/api/v1/auth/reset", "account"),
        ("POST", "/api/v1/invitations/accept", "account"),
        ("POST", "/api/v1/widget/chat", None),
        ("POST", "/api/v1/public/cob/chat", None),
        ("POST", f"/api/v1/public/learn/{ID}", "account"),
        ("POST", "/api/v1/query", None),
        ("POST", "/api/v1/query/stream", None),
        ("POST", "/api/v1/ingest", None),
        ("POST", f"/api/v1/corpora/{ID}/assistant/publish", None),
        ("POST", "/api/v1/auth/switch-tenant", None),
        ("POST", "/api/v1/auth/switch-admin", None),
        ("POST", "/api/v1/auth/change-password", "manage"),
        ("PATCH", "/api/v1/auth/me", "manage"),
        ("POST", "/api/v1/auth/sessions/revoke-others", "manage"),
        ("PATCH", "/api/v1/admin/settings", "manage"),
        ("POST", "/api/v1/admin/smtp/test", "manage"),
        ("POST", "/api/v1/admin/license/activate", "manage"),
        ("POST", "/api/v1/admin/license/deactivate", "manage"),
        ("PATCH", f"/api/v1/admin/plans/{ID}", "manage"),
        ("POST", "/api/v1/admin/plans", "manage"),
        ("POST", "/api/v1/admin/tenants", "manage"),
        ("POST", f"/api/v1/admin/tenants/{ID}/invitations", "manage"),
        ("POST", f"/api/v1/corpora/{ID}/keys", "manage"),
        ("POST", f"/api/v1/corpora/{ID}/whatsapp/embedded-signup", "manage"),
        ("PUT", f"/api/v1/corpora/{ID}/whatsapp/manual", "manage"),
        ("POST", f"/api/v1/admin/billing/accounts/{ID}/ledger", "manage"),
        ("POST", "/api/v1/billing/top-up-requests", "billing"),
        # P1 payments
        ("POST", "/api/v1/billing/payments/orders", "billing"),
        ("PUT", "/api/v1/billing/tax-details", "billing"),
        ("POST", "/api/v1/billing/payments/quote", None),
        ("PUT", "/api/v1/admin/billing/payments/settings", "manage"),
        # /P1 payments
        ("POST", f"/api/v1/corpora/{ID}/widget-keys", "keys"),
        ("PATCH", f"/api/v1/corpora/{ID}/widget-keys/{ID}", "keys"),
        ("POST", "/api/v1/webhooks", "outbound"),
        ("POST", f"/api/v1/webhooks/{ID}/test", "outbound"),
        ("POST", f"/api/v1/corpora/{ID}/whatsapp/threads/{ID}/reply", "whatsapp"),
        ("POST", f"/api/v1/corpora/{ID}/eval/runs", "cost"),
        ("PUT", f"/api/v1/corpora/{ID}/eval/settings", "secret"),
        ("POST", f"/api/v1/corpora/{ID}/assistant/setup/{ID}/tests", "cost"),
        ("GET", "/api/v1/auth/sessions", "privacy"),
        ("GET", "/api/v1/admin/audit", "privacy"),
        ("POST", "/api/v1/widget/lead", "privacy"),
        ("DELETE", f"/api/v1/corpora/{ID}", "delete"),
        ("DELETE", f"/api/v1/corpora/{ID}/assistant/draft", "delete"),  # a write DELETE
        ("DELETE", f"/api/v1/admin/tenants/{ID}", "delete"),
        ("POST", "/api/v1/not-a-route", "manage"),  # default deny
        ("GET", "/api/v1/admin/tenants", None),
        ("GET", "/api/v1/admin/license", None),
        ("HEAD", "/api/v1/health", None),
        ("OPTIONS", f"/api/v1/corpora/{ID}", None),
        ("GET", "/widget.js", None),
    ],
)
def test_pinned_answers(method: str, path: str, reason: str | None) -> None:
    assert guard.refusal(method, path, admin_key=False) == reason


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_the_admin_key_is_never_refused(method: str) -> None:
    assert guard.refusal(method, f"/api/v1/admin/tenants/{ID}", admin_key=True) is None


@pytest.mark.parametrize(("method", "template"), sorted(ROUTE_OPS))
def test_every_classified_route_follows_the_rule(method: str, template: str) -> None:
    path = _concrete(template)
    if required_operation(method, path) != ROUTE_OPS[(method, template)]:
        pytest.skip("a more specific template owns this concrete path")
    got = guard.refusal(method, path, admin_key=False)
    op = ROUTE_OPS[(method, template)]
    if (method, template) in guard.DEMO_REFUSED_READS:
        assert got == "privacy"
    elif method in {"GET", "HEAD"} or (method, template) in guard.DEMO_ALLOWED:
        assert got is None
    elif (method, template) in guard.DEMO_REFUSED:
        assert got == guard.DEMO_REFUSED[(method, template)]
    elif method == "DELETE" or op in {"delete", "manage"}:
        assert got in {"delete", "manage"}
    else:
        assert got is None


@pytest.mark.parametrize(("method", "template"), sorted(PUBLIC_ROUTES))
def test_public_routes(method: str, template: str) -> None:
    got = guard.refusal(method, _concrete(template), admin_key=False)
    key = (method, template)
    if key in guard.DEMO_REFUSED_READS:
        assert got == "privacy"
    elif method == "GET":
        assert got is None
    elif key in guard.DEMO_PRIVATE_PUBLIC:
        assert got == "privacy"
    else:
        assert got == ("account" if key in guard.DEMO_REFUSED_PUBLIC else None)


def test_every_table_row_names_a_real_route() -> None:
    from callobuzz_rag.api.app import create_app

    paths = {(m.upper(), p) for p, ops in create_app().openapi()["paths"].items() for m in ops}
    rows = (
        set(guard.DEMO_ALLOWED)
        | set(guard.DEMO_REFUSED)
        | set(guard.DEMO_REFUSED_PUBLIC)
        | set(guard.DEMO_REFUSED_FIELDS)
        | set(guard.DEMO_REFUSED_READS)
    )
    assert rows - paths == set()


def test_spec_list_is_covered() -> None:
    # "keys, deletes, e-mail settings, WhatsApp connect, licence changes, billing top-ups,
    #  plan edits, password/e-mail changes of the demo accounts"
    must_refuse = [
        ("POST", f"/api/v1/corpora/{ID}/keys"),
        ("DELETE", f"/api/v1/documents/{ID}"),
        ("POST", "/api/v1/admin/smtp/test"),
        ("POST", f"/api/v1/corpora/{ID}/whatsapp/embedded-signup"),
        ("POST", "/api/v1/admin/license/activate"),
        ("POST", "/api/v1/billing/top-up-requests"),
        ("PATCH", f"/api/v1/admin/plans/{ID}"),
        ("POST", "/api/v1/auth/change-password"),
        ("PATCH", "/api/v1/auth/me"),
    ]
    for method, path in must_refuse:
        assert guard.refusal(method, path, admin_key=False) is not None, (method, path)


def test_fields() -> None:
    assert guard.refused_field("POST", "/api/v1/corpora", {"name", "llm_config"}) == "llm_config"
    assert guard.refused_field("PUT", "/api/v1/corpora/{corpus_id}", {"name"}) is None
    # a concrete path finds its template too
    assert guard.refused_field("PUT", f"/api/v1/corpora/{ID}", {"llm_config"}) == "llm_config"


def test_embedding_config_is_refused_like_llm_config() -> None:
    for method, template in (("POST", "/api/v1/corpora"), ("PUT", "/api/v1/corpora/{corpus_id}")):
        assert guard.refused_field(method, template, {"name", "embedding_config"}) == (
            "embedding_config"
        )
    assert guard.refused_field("POST", "/api/v1/corpora", {"name"}) is None
