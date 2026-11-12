"""Rendering transactional e-mail from the Jinja2 templates in `templates/`.

Every template extends `_base.html` / `_base.txt`, whose footer prints the operator's
branding and, as its last line, the constant `POWERED_BY` ("cob-rag-engine by Callobuzz").
That line lives in the layout and is a Jinja global, not context or a setting, and
context may not shadow it, so no template, caller or configuration can drop it.

HTML is autoescaped (business and person names are user input); every template runs
with `StrictUndefined`, so a missing context key is an error, never a blank. Subjects
are plain text: rendered without escaping, then collapsed onto one line so a name with a
newline cannot inject a header.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any
from urllib.parse import urlencode

from jinja2 import (
    Environment,
    PackageLoader,
    StrictUndefined,
    TemplateError,
    select_autoescape,
)

from callobuzz_rag.accounts.email.base import OutgoingEmail
from callobuzz_rag.accounts.settings_store import Branding, check_url, powered_by_for
from callobuzz_rag.core.exceptions import EmailTemplateError


@dataclass(frozen=True)
class TemplateSpec:
    """A template's subject (a Jinja string) and the context keys it needs."""

    subject: str
    required: frozenset[str]


def _spec(subject: str, *keys: str) -> TemplateSpec:
    return TemplateSpec(subject=subject, required=frozenset(keys))


TEMPLATES: dict[str, TemplateSpec] = {
    "verify_code": _spec(
        "Your {{ product }} verification code: {{ code }}", "name", "code", "minutes"
    ),
    "reset_code": _spec("Your {{ product }} password reset code", "name", "code", "minutes"),
    "superadmin_setup": _spec("Set your {{ product }} admin password", "code", "link", "hours"),
    "account_exists": _spec(
        "Someone tried to sign up with your e-mail", "name", "login_url", "reset_url"
    ),
    "password_changed": _spec("Your {{ product }} password was changed", "name", "when"),
    "invitation": _spec(
        "{{ inviter_name }} invited you to {{ tenant_name }} on {{ product }}",
        "inviter_name",
        "tenant_name",
        "role",
        "accept_url",
        "days",
    ),
    "signup_notice": _spec(
        "New sign-up: {{ tenant_name }}",
        "tenant_name",
        "owner_name",
        "owner_email",
        "customers_url",
    ),
    "activated": _spec(
        "You're in: {{ tenant_name }} is active",
        "tenant_name",
        "plan_name",
        "login_url",
        "reactivated",
    ),
    "suspended": _spec("{{ tenant_name }} has been suspended", "tenant_name"),
    "declined": _spec("Your {{ product }} sign-up for {{ tenant_name }}", "tenant_name"),
    "smtp_test": _spec("{{ product }} test e-mail", "sent_at"),
    "lead_new": _spec(
        "New lead from {{ kb_name }}: {{ name or phone or email }}",
        "kb_name",
        "business_name",
        "name",
        "phone",
        "email",
        "custom",
        "summary",
        "language",
        "page_url",
        "trigger",
        "sources",
        "lead_url",
    ),
    "lead_digest": _spec(
        "{{ total }} new lead{{ 's' if total != 1 else '' }} for {{ business_name }} — {{ day }}",
        "business_name",
        "kb_name",
        "day",
        "total",
        "by_status",
        "by_qualification",
        "rows",
        "more",
        "leads_url",
    ),
    # BIZ1 website
    "website_learned": _spec(
        "{{ business_name }}: your website is ready",
        "business_name",
        "product_name",
        "site",
        "pages_learned",
        "pages_skipped",
        "knowledge_url",
    ),
    # /BIZ1 website
    # BIL1 provider alerts
    "provider_low_balance": _spec(
        "{{ provider_label }} balance is {{ 'empty' if status == 'empty' else 'low' }}",
        "provider_label",
        "status",
        "remaining",
        "threshold",
        "costs_url",
    ),
    # /BIL1 provider alerts
    # BIL4 alerts
    "billing_low_balance": _spec(
        "{{ tenant_name }}: your balance is running low",
        "tenant_name",
        "level_pct",
        "balance",
        "payment_type",
        "billing_url",
    ),
    "billing_paused": _spec(
        "{{ tenant_name }}: answers are paused",
        "tenant_name",
        "reason",
        "billing_url",
        "support_email",
    ),
    # /BIL4 alerts
    # TV6 whatsapp
    "whatsapp_attention": _spec(
        "{{ tenant_name }}: WhatsApp needs your attention",
        "tenant_name",
        "number",
        "reason",
        "whatsapp_url",
    ),
    # /TV6 whatsapp
    # PIP3 fair use
    "fair_use_alert": _spec(
        "{{ tenant_name }}: {{ metric_label }} at {{ level_pct }}%",
        "tenant_name",
        "metric_label",
        "level_pct",
        "used",
        "limit",
        "what_happens",
        "resets_at",
        "plan_url",
    ),
    "fair_use_alert_admin": _spec(
        "{{ tenant_name }}: {{ metric_label }} at {{ level_pct }}%",
        "tenant_name",
        "metric_label",
        "level_pct",
        "used",
        "limit",
        "what_happens",
        "customer_url",
    ),
    "engine_spend_alert": _spec(
        "AI spend today is at {{ level_pct }}% of your ceiling",
        "level_pct",
        "used",
        "limit",
        "costs_url",
    ),
    # /PIP3 fair use
    # BIL6 top-up requests
    "billing_top_up_request": _spec(
        "{{ tenant_name }} asks for a top-up",
        "tenant_name",
        "owner_name",
        "owner_email",
        "amount",
        "note",
        "account_url",
    ),
    # /BIL6 top-up requests
    "handoff_new": _spec(
        "{{ visitor_label }} wants to talk to {{ business_name or kb_name }}",
        "business_name",
        "kb_name",
        "visitor_label",
        "channel_label",
        "reason_label",
        "summary",
        "question",
        "language",
        "page_url",
        "office_open",
        "phone",
        "email",
        "transcript_url",
    ),
    # TV4 value
    "value_report": _spec(
        "What your assistant did in {{ month_label }}",
        "business_name",
        "product_name",
        "month_label",
        "total",
        "parts",
        "conversations",
        "answered",
        "after_hours",
        "leads_qualified",
        "leads_label",
        "handoffs",
        "bookings",
        "first_reply",
        "top_questions",
        "unanswered",
        "report_url",
        "settings_url",
        "saved_settings",
    ),
    # /TV4 value
    # TV3 notice
    "billing_renewal_reminder": _spec(
        "{{ business_name }}: your plan renews on {{ renews_on }}",
        "business_name",
        "product_name",
        "renews_on",
        "amount",
        "plan_name",
        "included",
        "new_price",
        "billing_url",
    ),
    "billing_price_change": _spec(
        "{{ business_name }}: a price change for your plan",
        "business_name",
        "product_name",
        "plan_name",
        "effective_on",
        "changes",
        "lower",
        "billing_url",
        "support_url",
    ),
    "billing_price_change_cancelled": _spec(
        "{{ business_name }}: the price change was called off",
        "business_name",
        "product_name",
        "plan_name",
        "billing_url",
    ),
    # /TV3 notice
    # TV3 support
    "support_request_new": _spec(
        "Support request #{{ number }} from {{ business_name }}: {{ request_subject }}",
        "number",
        "request_subject",
        "business_name",
        "from_email",
        "message",
        "page",
        "admin_url",
    ),
    "support_reply": _spec(
        "Re: #{{ number }} {{ request_subject }}",
        "number",
        "request_subject",
        "product_name",
        "message",
        "request_url",
    ),
    # /TV3 support
    # P1 payments
    "payment_received": _spec(
        "Payment received: {{ amount }}",
        "tenant_name",
        "amount",
        "purpose_label",
        "balance",
        "billing_url",
    ),
    "payment_link": _spec(
        "{{ tenant_name }}: pay {{ amount }} online",
        "tenant_name",
        "amount",
        "purpose_label",
        "pay_url",
        "expires_on",
    ),
    "refund_issued": _spec(
        "Refund of {{ amount }} on its way", "tenant_name", "amount", "billing_url"
    ),
    "payment_attention": _spec(
        "{{ tenant_name }}: a payment needs a look", "tenant_name", "reason", "payments_url"
    ),
    # /P1 payments
    # P2 invoices
    "invoice_issued": _spec(
        "Tax document {{ number }}: {{ total }}",
        "tenant_name",
        "number",
        "total",
        "invoices_url",
    ),
    # /P2 invoices
    # P5 plans
    "service_order_update": _spec(
        "{{ service_name }}: {{ status_label }}",
        "tenant_name",
        "service_name",
        "status_label",
        "public_note",
        "orders_url",
    ),
    "add_on_ended": _spec(
        "{{ add_on_name }} has ended", "tenant_name", "add_on_name", "reason", "shop_url"
    ),
    # /P5 plans
}

# Context keys that become links: each must be an absolute http(s) URL.
URL_KEYS = frozenset(
    {
        "link",
        "login_url",
        "reset_url",
        "accept_url",
        "customers_url",
        "lead_url",
        "leads_url",
        "transcript_url",
        "costs_url",  # BIL1
        "billing_url",  # BIL4
        "account_url",  # BIL6
        "knowledge_url",  # BIZ1
        "plan_url",  # PIP3
        "customer_url",  # PIP3
        "report_url",  # TV4
        "settings_url",  # TV4
        "support_url",  # TV3
        "request_url",  # TV3
        "admin_url",  # TV3
        "pay_url",  # P1 payments
        "payments_url",  # P1 payments
        "invoices_url",  # P2 invoices
        "orders_url",  # P5 plans
        "shop_url",  # P5 plans
    }
)
# Names the layout provides; a caller's context may not shadow them.
RESERVED_KEYS = frozenset({"branding", "dashboard_url", "product", "subject", "POWERED_BY"})


@cache
def _environments() -> tuple[Environment, Environment]:
    """(body environment, subject environment), built once per process."""
    body = Environment(
        loader=PackageLoader("callobuzz_rag.accounts.email", "templates"),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    subject = Environment(autoescape=False, undefined=StrictUndefined)
    return body, subject


def dashboard_link(dashboard_url: str, path: str, fragment: Mapping[str, str] | None = None) -> str:
    """An absolute dashboard link. Secrets go in `fragment` (`#code=...`), which browsers
    never send to a server, so they stay out of every access log."""
    url = f"{dashboard_url.rstrip('/')}/{path.lstrip('/')}"
    return f"{url}#{urlencode(fragment)}" if fragment else url


def validate_context(name: str, to: str, context: Mapping[str, Any]) -> TemplateSpec:
    """Refuse an unknown template, a bad recipient or context before anything is queued.

    Raises:
        EmailTemplateError: with a message naming the problem (never the context values).
    """
    spec = TEMPLATES.get(name)
    if spec is None:
        raise EmailTemplateError(f"unknown e-mail template {name!r}")
    if not to or any(ch in to for ch in "\r\n") or "@" not in to:
        raise EmailTemplateError("invalid recipient address")
    missing = spec.required - context.keys()
    if missing:
        raise EmailTemplateError(f"template {name!r} is missing {', '.join(sorted(missing))}")
    reserved = RESERVED_KEYS & context.keys()
    if reserved:
        raise EmailTemplateError(f"context may not set {', '.join(sorted(reserved))}")
    for key in URL_KEYS & context.keys():
        value = context[key]
        try:
            ok = isinstance(value, str) and check_url(value, allow_http=True) is not None
        except ValueError:
            ok = False
        if not ok:
            raise EmailTemplateError(f"{key} must be an absolute http(s) URL")
    try:
        json.dumps(dict(context))
    except (TypeError, ValueError) as exc:
        raise EmailTemplateError("context must be JSON-serialisable") from exc
    return spec


def render_email(
    name: str,
    to: str,
    context: Mapping[str, Any],
    branding: Branding,
    dashboard_url: str,
) -> OutgoingEmail:
    """Render template `name` for `to` with the operator's branding.

    Raises:
        EmailTemplateError: unknown template, bad recipient, missing/invalid context, or
            a template that fails to render.
    """
    spec = validate_context(name, to, context)
    body_env, subject_env = _environments()
    values: dict[str, Any] = {
        **context,
        "branding": branding,
        "product": branding.product_name,
        "dashboard_url": dashboard_url.rstrip("/"),
        "POWERED_BY": powered_by_for(branding),  # None when Branding hides it
    }
    try:
        subject = " ".join(subject_env.from_string(spec.subject).render(values).split())
        values["subject"] = subject
        html = body_env.get_template(f"{name}.html").render(values)
        text = body_env.get_template(f"{name}.txt").render(values)
    except TemplateError as exc:
        raise EmailTemplateError(f"template {name!r} failed to render: {exc}") from exc
    return OutgoingEmail(to=to, subject=subject, text=text, html=html)
