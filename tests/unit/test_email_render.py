"""E-mail templates: every one renders, escapes, and ends with the attribution line (accounts A2)."""

import re
from pathlib import Path
from typing import Any

import pytest

from callobuzz_rag.accounts.email.render import (
    RESERVED_KEYS,
    TEMPLATES,
    dashboard_link,
    render_email,
)
from callobuzz_rag.accounts.settings_store import POWERED_BY, Branding
from callobuzz_rag.core.exceptions import EmailTemplateError

DASH = "https://app.sahayak.example"
TO = "owner@shree.example"

SAMPLES: dict[str, dict[str, Any]] = {
    "verify_code": {"name": "Asha", "code": "482913", "minutes": 10},
    "reset_code": {"name": "Asha", "code": "482913", "minutes": 10},
    "superadmin_setup": {
        "code": "482913",
        "link": dashboard_link(DASH, "/set-password", {"code": "482913"}),
        "hours": 24,
    },
    "account_exists": {
        "name": "Asha",
        "login_url": dashboard_link(DASH, "login"),
        "reset_url": dashboard_link(DASH, "forgot"),
    },
    "password_changed": {"name": "Asha", "when": "30 Sep 2026, 17:40 UTC"},
    "invitation": {
        "inviter_name": "Ravi",
        "tenant_name": "Shree Labs",
        "role": "member",
        "accept_url": dashboard_link(DASH, "invite", {"token": "tok"}),
        "days": 7,
    },
    "signup_notice": {
        "tenant_name": "Shree Labs",
        "owner_name": "Asha",
        "owner_email": TO,
        "customers_url": dashboard_link(DASH, "customers"),
    },
    "activated": {
        "tenant_name": "Shree Labs",
        "plan_name": "Starter",
        "login_url": dashboard_link(DASH, "login"),
        "reactivated": False,
    },
    # BIZ1 website
    "website_learned": {
        "business_name": "Shree Dental Clinic",
        "product_name": "Sahayak AI",
        "site": "shreedental.in",
        "pages_learned": 14,
        "pages_skipped": 3,
        "knowledge_url": dashboard_link(DASH, "knowledge"),
    },
    # /BIZ1 website
    # P1 payments
    "payment_received": {
        "tenant_name": "Shree Labs",
        "amount": "₹1,180.00",
        "purpose_label": "Add money to your balance",
        "balance": "₹1,450.00",
        "billing_url": dashboard_link(DASH, "plan"),
    },
    "payment_link": {
        "tenant_name": "Shree Labs",
        "amount": "₹1,180.00",
        "purpose_label": "Add money to your balance",
        "pay_url": dashboard_link(DASH, "pay", {"order": "abc"}),
        "expires_on": "15 Oct 2026",
    },
    "refund_issued": {
        "tenant_name": "Shree Labs",
        "amount": "₹590.00",
        "billing_url": dashboard_link(DASH, "plan"),
    },
    "payment_attention": {
        "tenant_name": "Shree Labs",
        "reason": "The payment is ₹10.00 but the order was ₹1,180.00.",
        "payments_url": dashboard_link(DASH, "admin/billing"),
    },
    # /P1 payments
    # P2 invoices
    "invoice_issued": {
        "tenant_name": "Shree Labs",
        "number": "INV/26-27/00001",
        "total": "₹1,178.82",
        "invoices_url": dashboard_link(DASH, "plan"),
    },
    # /P2 invoices
    # P5 plans
    "service_order_update": {
        "tenant_name": "Shree Labs",
        "service_name": "Installation help",
        "status_label": "In progress",
        "public_note": "We will call you on Monday to set up the widget.",
        "orders_url": dashboard_link(DASH, "plan"),
    },
    "add_on_ended": {
        "tenant_name": "Shree Labs",
        "add_on_name": "Use your own AI key",
        "reason": "Your balance was too low to renew it.",
        "shop_url": dashboard_link(DASH, "plan"),
    },
    # /P5 plans
    "suspended": {"tenant_name": "Shree Labs"},
    "declined": {"tenant_name": "Shree Labs"},
    "smtp_test": {"sent_at": "2026-09-30 12:00:00 UTC"},
    "lead_new": {
        "kb_name": "Patient help",
        "business_name": "Shree Labs",
        "name": "Rohan",
        "phone": "+919812345678",
        "email": "",
        "custom": [{"label": "Test", "value": "HbA1c"}],
        "summary": "Wants a home visit.",
        "language": "en",
        "page_url": "",
        "trigger": "buying_intent",
        "sources": ["home-collection.pdf"],
        "lead_url": dashboard_link(DASH, "leads", {}) + "?lead=1",
    },
    "lead_digest": {
        "business_name": "Shree Labs",
        "kb_name": "Patient help",
        "day": "1 Oct 2026",
        "total": 2,
        "by_status": {"new": 2},
        "by_qualification": {"qualified": 1, "none": 1},
        "rows": [
            {
                "name": "Priya Kulkarni",
                "phone": "+919812345678",
                "status": "new",
                "qualification": "qualified",
                "created_local": "09:15",
            },
        ],
        "more": 0,
        "leads_url": dashboard_link(DASH, "leads"),
    },
    "handoff_new": {
        "business_name": "Shree Labs",
        "kb_name": "Patient help",
        "visitor_label": "Rohan",
        "channel_label": "WhatsApp",
        "reason_label": "Asked for a person",
        "summary": "Wants a refund for a cancelled home visit.",
        "question": "Mera home collection cancel hua, refund chahiye",
        "language": "hi-Latn",
        "page_url": "https://shree-labs.in/tests",
        "office_open": "open",
        "phone": "",
        "email": "",
        "transcript_url": dashboard_link(DASH, "leads?kb=k&tab=handoffs&handoff=h"),
    },
    "provider_low_balance": {  # BIL1
        "provider_label": "OpenRouter",
        "status": "low",
        "remaining": "$3.20",
        "threshold": "$5.00",
        "costs_url": dashboard_link(DASH, "admin/costs"),
    },
    "billing_low_balance": {  # BIL4
        "tenant_name": "Shree Labs",
        "level_pct": 20,
        "balance": "₹19.00",
        "payment_type": "prepaid",
        "billing_url": dashboard_link(DASH, "plan"),
    },
    "billing_paused": {  # BIL4
        "tenant_name": "Shree Labs",
        "reason": "prepaid_empty",
        "billing_url": dashboard_link(DASH, "plan"),
        "support_email": "help@op.example",
    },
    "whatsapp_attention": {  # TV6
        "tenant_name": "Shree Labs",
        "number": "+91 98000 00000",
        "reason": "token",
        "whatsapp_url": dashboard_link(DASH, "whatsapp"),
    },
    "fair_use_alert": {  # PIP3
        "tenant_name": "Shree Dental Clinic, Pune",
        "metric_label": "questions this hour",
        "level_pct": 80,
        "used": "24",
        "limit": "30",
        "what_happens": "Nothing changes yet. This is a heads-up so you can plan.",
        "resets_at": "05 Oct 2026, 15:00 UTC",
        "plan_url": dashboard_link(DASH, "plan"),
    },
    "fair_use_alert_admin": {  # PIP3
        "tenant_name": "Shree Dental Clinic, Pune",
        "metric_label": "AI usage today",
        "level_pct": 100,
        "used": "$5.00",
        "limit": "$5.00",
        "what_happens": "Answers continue, but more slowly until 06 Oct, 00:00 UTC.",
        "customer_url": dashboard_link(DASH, "customers/t1"),
    },
    "engine_spend_alert": {  # PIP3
        "level_pct": 80,
        "used": "$40.00",
        "limit": "$50.00",
        "costs_url": dashboard_link(DASH, "costs"),
    },
    "billing_top_up_request": {  # BIL6
        "tenant_name": "Shree Labs",
        "owner_name": "Asha",
        "owner_email": "asha@shree.example",
        "amount": "₹5,000.00",
        "note": "UPI sent this morning",
        "account_url": dashboard_link(DASH, "admin/customers/t1?tab=billing"),
    },
    # TV4 value
    "value_report": {
        "business_name": "Shree Dental Clinic, Pune",
        "product_name": "Sahayak AI",
        "month_label": "October 2026",
        "total": "₹37,050",
        "parts": [
            {
                "label": "Qualified leads",
                "amount": "₹13,500",
                "formula": "9 qualified leads × ₹1,500 = ₹13,500",  # noqa: RUF001
            },
            {
                "label": "Requests to talk to a person",
                "amount": "₹18,400",
                "formula": "23 requests to talk to a person × ₹800 = ₹18,400",  # noqa: RUF001
            },
            {
                "label": "Staff time saved",
                "amount": "₹5,150",
                "formula": "412 questions answered × 3 min saved = 20 h 36 min × ₹250/h",  # noqa: RUF001
            },
        ],
        "conversations": 188,
        "answered": 412,
        "after_hours": 61,
        "leads_qualified": 9,
        "leads_label": "qualified leads",
        "handoffs": 23,
        "bookings": 14,
        "first_reply": "under a second",
        "top_questions": [{"question": "root canal ka kitna charge hai?", "count": 17}],
        "unanswered": [{"question": "do you take cards", "count": 4}],
        "report_url": dashboard_link(DASH, "value?month=2026-10"),
        "settings_url": dashboard_link(DASH, "value?edit=1"),
        "saved_settings": True,
    },
    # /TV4 value
    # TV3 notice
    "billing_renewal_reminder": {
        "business_name": "Shree Dental Clinic, Pune",
        "product_name": "Sahayak AI",
        "renews_on": "12 October 2026",
        "amount": "₹999",
        "plan_name": "Growth",
        "included": "₹500",
        "new_price": True,
        "billing_url": dashboard_link(DASH, "plan"),
    },
    "billing_price_change": {
        "business_name": "Shree Dental Clinic, Pune",
        "product_name": "Sahayak AI",
        "plan_name": "Growth",
        "effective_on": "12 November 2026",
        "changes": [
            {"label": "Plan fee", "old": "₹999", "new": "₹1,299"},
            {"label": "Price per question", "old": "₹2", "new": "₹3"},
        ],
        "lower": False,
        "billing_url": dashboard_link(DASH, "plan"),
        "support_url": dashboard_link(DASH, "support"),
    },
    "billing_price_change_cancelled": {
        "business_name": "Shree Dental Clinic, Pune",
        "product_name": "Sahayak AI",
        "plan_name": "Growth",
        "billing_url": dashboard_link(DASH, "plan"),
    },
    # /TV3 notice
    # TV3 support
    "support_request_new": {
        "number": 1042,
        "request_subject": "WhatsApp button not showing",
        "business_name": "Shree Dental Clinic, Pune",
        "from_email": "asha@shreedental.in",
        "message": "Since yesterday the widget has no button. <b>Please</b> check.",
        "page": "/widget",
        "admin_url": dashboard_link(DASH, "admin/support?request=r1"),
    },
    "support_reply": {
        "number": 1042,
        "request_subject": "WhatsApp button not showing",
        "product_name": "Sahayak AI",
        "message": "Fixed: your plan now includes the widget.",
        "request_url": dashboard_link(DASH, "support?request=r1"),
    },
    # /TV3 support
}

CUSTOM = Branding(
    product_name="Sahayak AI",
    logo_url="https://cdn.sahayak.example/logo.png",
    primary_color="#0a7d5a",
    support_email="help@sahayak.example",
)
TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src/callobuzz_rag/accounts/email/templates"


def test_value_report_only_claims_the_owners_values_when_they_saved_some() -> None:
    saved = render_email("value_report", TO, SAMPLES["value_report"], Branding(), DASH)
    assert "based on your settings" in saved.text and "values you set" in saved.html
    unsaved_ctx = {**SAMPLES["value_report"], "saved_settings": False}
    unsaved = render_email("value_report", TO, unsaved_ctx, Branding(), DASH)
    for body in (unsaved.text, unsaved.html):
        assert "suggested values" in body
        assert "values you set" not in body and "based on your settings" not in body


def test_samples_cover_every_template_exactly() -> None:
    assert set(SAMPLES) == set(TEMPLATES)
    assert (
        len(TEMPLATES)
        == 36  # P1 payments: four payment mails, P2 invoices: invoice_issued, P5 plans: two
    )  # 19 + fair-use mails (PIP3) + value report (TV4) + TV3 (5) + TV6 owner alert
    for name, spec in TEMPLATES.items():
        assert set(SAMPLES[name]) == spec.required, name
        assert (TEMPLATE_DIR / f"{name}.html").is_file()
        assert (TEMPLATE_DIR / f"{name}.txt").is_file()


def _powered_by_is_last(html: str, text: str) -> None:
    label = re.escape(POWERED_BY["text"])
    assert re.search(rf"{label}(\s*</[a-z]+>)*\s*$", html), html[-400:]
    assert not re.search(rf"<a[^>]*>\s*{label}", html)  # plain text, never a link
    assert text.rstrip().splitlines()[-1] == POWERED_BY["text"]


@pytest.mark.parametrize("branding", [Branding(), CUSTOM], ids=["default", "custom"])
@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_every_template_renders_and_ends_with_powered_by(name: str, branding: Branding) -> None:
    email = render_email(name, TO, SAMPLES[name], branding, DASH)
    assert email.to == TO
    assert email.subject and "\n" not in email.subject and "{{" not in email.subject
    assert email.html.lstrip().startswith("<!doctype html>")
    assert "{{" not in email.html and "{%" not in email.html
    assert "{{" not in email.text and "{%" not in email.text
    assert branding.product_name in email.text
    assert branding.primary_color in email.html
    _powered_by_is_last(email.html, email.text)


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_custom_branding_appears(name: str) -> None:
    email = render_email(name, TO, SAMPLES[name], CUSTOM, DASH + "/")
    assert f'src="{CUSTOM.logo_url}"' in email.html
    assert 'alt="Sahayak AI"' in email.html
    assert "help@sahayak.example" in email.html
    assert "help@sahayak.example" in email.text
    assert f'href="{DASH}"' in email.html  # trailing slash trimmed
    assert DASH in email.text
    assert "cob-rag-engine" not in email.subject


@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_every_link_uses_the_dashboard_url(name: str) -> None:
    email = render_email(name, TO, SAMPLES[name], Branding(), DASH)
    links = re.findall(r'href="([^"]+)"', email.html)
    for link in links:
        assert link.startswith((DASH, "mailto:")), link


def test_subjects_name_the_product() -> None:
    email = render_email("verify_code", TO, SAMPLES["verify_code"], CUSTOM, DASH)
    assert email.subject == "Your Sahayak AI verification code: 482913"
    invite = render_email("invitation", TO, SAMPLES["invitation"], CUSTOM, DASH)
    assert invite.subject == "Ravi invited you to Shree Labs on Sahayak AI"


def test_codes_are_large_and_monospaced_in_html_and_alone_in_text() -> None:
    email = render_email("verify_code", TO, SAMPLES["verify_code"], Branding(), DASH)
    assert re.search(r"monospace;font-size:30px[^>]*>482913<", email.html)
    assert "482913" in [line.strip() for line in email.text.splitlines()]


def test_secrets_ride_in_the_fragment() -> None:
    assert dashboard_link(DASH + "/", "/invite", {"token": "a b"}) == f"{DASH}/invite#token=a+b"
    assert dashboard_link(DASH, "login") == f"{DASH}/login"


def test_user_supplied_names_are_escaped_in_html() -> None:
    context = {**SAMPLES["signup_notice"], "tenant_name": "<script>alert(1)</script>"}
    email = render_email("signup_notice", TO, context, Branding(), DASH)
    assert "<script>" not in email.html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in email.html
    assert "<script>alert(1)</script>" in email.text  # plain text is not HTML
    assert email.subject == "New sign-up: <script>alert(1)</script>"  # plain header


def test_a_newline_in_a_name_cannot_inject_a_header() -> None:
    context = {**SAMPLES["suspended"], "tenant_name": "Shree\r\nBcc: victim@evil.example"}
    email = render_email("suspended", TO, context, Branding(), DASH)
    assert "\r" not in email.subject and "\n" not in email.subject
    assert email.subject == "Shree Bcc: victim@evil.example has been suspended"


def test_missing_key_is_an_error() -> None:
    context = dict(SAMPLES["verify_code"])
    del context["code"]
    with pytest.raises(EmailTemplateError, match="missing code"):
        render_email("verify_code", TO, context, Branding(), DASH)


def test_unknown_template_is_an_error() -> None:
    with pytest.raises(EmailTemplateError, match="unknown"):
        render_email("welcome", TO, {}, Branding(), DASH)


@pytest.mark.parametrize("to", ["", "no-at-sign", "a@b.example\r\nBcc: x@y.example"])
def test_bad_recipient_is_an_error(to: str) -> None:
    with pytest.raises(EmailTemplateError, match="recipient"):
        render_email("smtp_test", to, SAMPLES["smtp_test"], Branding(), DASH)


@pytest.mark.parametrize("key", sorted(RESERVED_KEYS))
def test_context_cannot_shadow_the_layout(key: str) -> None:
    context = {**SAMPLES["smtp_test"], key: {"text": "", "url": ""}}
    with pytest.raises(EmailTemplateError, match="may not set"):
        render_email("smtp_test", TO, context, Branding(), DASH)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "/login", "ftp://x.example/a"])
def test_links_must_be_absolute_http(url: str) -> None:
    context = {**SAMPLES["account_exists"], "login_url": url}
    with pytest.raises(EmailTemplateError, match="login_url"):
        render_email("account_exists", TO, context, Branding(), DASH)


def test_activated_variants() -> None:
    base = SAMPLES["activated"]
    fresh = render_email("activated", TO, {**base, "plan_name": None}, Branding(), DASH)
    assert "You're in" in fresh.text and "Your plan" not in fresh.text
    again = render_email("activated", TO, {**base, "reactivated": True}, Branding(), DASH)
    assert "active again" in again.text and "Starter" in again.text


@pytest.mark.parametrize(
    ("reason", "says", "never"),
    [
        ("prepaid_empty", "the balance has run out", "credit limit"),
        ("credit_limit", "the credit limit is reached", "balance has run out"),
        ("forced", "by Sahayak AI", "balance"),
    ],
)
def test_the_paused_mail_gives_the_real_reason(reason: str, says: str, never: str) -> None:
    ctx = {**SAMPLES["billing_paused"], "reason": reason}
    email = render_email("billing_paused", TO, ctx, CUSTOM, DASH)
    for body in (email.html, email.text):
        assert says in body and never not in body


def test_without_support_email_the_team_is_named() -> None:
    email = render_email("declined", TO, SAMPLES["declined"], Branding(), DASH)
    assert "contact the cob-rag-engine team" in email.text
    assert "mailto:" not in email.html


def test_no_template_can_define_its_own_footer() -> None:
    """The footer is outside every block, so only `content` can be overridden."""
    base = (TEMPLATE_DIR / "_base.html").read_text(encoding="utf-8")
    blocks = re.findall(r"{%\s*block\s+(\w+)", base)
    assert blocks == ["content"]
    assert base.index("POWERED_BY") > base.index("{% endblock %}")
    base_txt = (TEMPLATE_DIR / "_base.txt").read_text(encoding="utf-8")
    assert re.findall(r"{%\s*block\s+(\w+)", base_txt) == ["content"]
    # the line is omitted when the operator hides it in Branding
    assert base_txt.rstrip().endswith("{% if POWERED_BY %}\n{{ POWERED_BY.text }}\n{% endif %}")
    for name in TEMPLATES:
        for ext in ("html", "txt"):
            source = (TEMPLATE_DIR / f"{name}.{ext}").read_text(encoding="utf-8")
            assert source.startswith(f'{{% extends "_base.{ext}" %}}'), (name, ext)
            assert "POWERED_BY" not in source


def test_lead_new_subject_names_the_contact_even_without_a_name() -> None:
    sample = SAMPLES["lead_new"]
    named = render_email("lead_new", TO, sample, Branding(), DASH)
    assert named.subject == "New lead from Patient help: Rohan"
    assert "+919812345678" in named.text and "HbA1c" in named.html
    phone_only = render_email("lead_new", TO, {**sample, "name": ""}, Branding(), DASH)
    assert phone_only.subject == "New lead from Patient help: +919812345678"
    email_only = render_email(
        "lead_new", TO, {**sample, "name": "", "phone": "", "email": "a@b.in"}, Branding(), DASH
    )
    assert email_only.subject == "New lead from Patient help: a@b.in"


def test_lead_new_escapes_what_a_visitor_typed() -> None:
    sample = {**SAMPLES["lead_new"], "name": "<script>alert(1)</script>"}
    html = render_email("lead_new", TO, sample, Branding(), DASH).html
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_lead_url_keys_must_be_absolute() -> None:
    from callobuzz_rag.accounts.email.render import URL_KEYS, validate_context

    assert {"lead_url", "leads_url"} <= URL_KEYS  # leads_url: C9's digest (README P14)
    with pytest.raises(EmailTemplateError):
        validate_context("lead_new", TO, {**SAMPLES["lead_new"], "lead_url": "javascript:alert(1)"})


def test_lead_digest_lists_rows_and_escapes_names() -> None:
    sample = SAMPLES["lead_digest"]
    mail = render_email("lead_digest", TO, sample, Branding(), DASH)
    assert mail.subject == "2 new leads for Shree Labs \u2014 1 Oct 2026"
    assert "Priya Kulkarni" in mail.html and "+919812345678" in mail.text
    rows = [{**sample["rows"][0], "name": "<script>x</script>"}]
    html = render_email("lead_digest", TO, {**sample, "rows": rows}, Branding(), DASH).html
    assert "<script>x" not in html
