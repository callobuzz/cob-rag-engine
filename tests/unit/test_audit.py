"""record_audit: actor detection, the action catalog, secret scrubbing (accounts A7)."""

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.accounts.audit import AUDIT_ACTIONS, _scrub, record_audit
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.audit_log import AuditLog


def _request(trust_proxy: bool = False, **state: Any) -> Any:
    settings = Settings(_env_file=None, widget_trust_proxy_headers=trust_proxy)
    return SimpleNamespace(
        state=SimpleNamespace(**state),
        app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
        headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"},
        client=SimpleNamespace(host="192.0.2.4"),
    )


def _session() -> Any:
    session = MagicMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    return session


async def _row(request: Any, **kwargs: Any) -> AuditLog:
    session = _session()
    await record_audit(session, request, "settings.updated", **kwargs)
    session.add.assert_called_once()
    session.commit.assert_not_awaited()
    session.flush.assert_not_awaited()
    row = session.add.call_args.args[0]
    assert isinstance(row, AuditLog)
    return row


async def test_admin_key() -> None:
    row = await _row(_request(is_admin=True, tenant=None, corpus=None))
    assert (row.actor_kind, row.actor_user_id, row.actor_label) == ("admin_key", None, "admin key")
    assert row.ip == "192.0.2.4"  # proxy headers untrusted by default


async def test_tenant_key() -> None:
    tenant = SimpleNamespace(id=uuid.uuid4(), name="Shree Labs")
    row = await _row(_request(is_admin=False, tenant=tenant, corpus=None))
    assert (row.actor_kind, row.actor_label) == ("tenant_key", "tenant key Shree Labs")


async def test_corpus_key() -> None:
    corpus = SimpleNamespace(id=uuid.uuid4(), name="Support docs")
    row = await _row(_request(is_admin=False, tenant=None, corpus=corpus))
    assert (row.actor_kind, row.actor_label) == ("corpus_key", "corpus key Support docs")


async def test_signed_in_user_wins_over_the_scope() -> None:
    user = SimpleNamespace(id=uuid.uuid4(), email="owner@shree.example")
    row = await _row(_request(is_admin=True, tenant=None, corpus=None, user=user))
    assert (row.actor_kind, row.actor_user_id, row.actor_label) == (
        "user",
        user.id,
        "owner@shree.example",
    )


async def test_without_a_request_the_actor_is_the_system() -> None:
    row = await _row(None)
    assert (row.actor_kind, row.actor_user_id, row.actor_label) == ("system", None, "system")
    assert row.ip is None


async def test_explicit_actor_and_fields() -> None:
    tid = uuid.uuid4()
    uid = uuid.uuid4()
    row = await _row(
        _request(trust_proxy=True, is_admin=True),
        actor=("user", uid, "boss@operator.example"),
        tenant_id=tid,
        tenant_label="Shree Labs",
        target_type="tenant",
        target_id=str(tid),
        details={"from": "pending", "to": "active", "note": "paid ₹3,000 via UPI"},
    )
    assert (row.actor_kind, row.actor_user_id) == ("user", uid)
    assert (row.tenant_id, row.tenant_label, row.target_type) == (tid, "Shree Labs", "tenant")
    assert row.details == {"from": "pending", "to": "active", "note": "paid ₹3,000 via UPI"}
    # Trusted, one hop: the entry the proxy appended (the last), never the caller's first.
    assert row.ip == "10.0.0.1"


async def test_unknown_action_asserts() -> None:
    with pytest.raises(AssertionError, match="unknown audit action"):
        await record_audit(_session(), None, "settings.changed")


def test_catalog_is_the_full_list() -> None:
    # The catalog in the A7 spec; later epics use exactly these strings.
    assert {
        "settings.updated",
        "superadmin.bootstrapped",
        "user.password_reset",
        "user.password_changed",
        "user.login_locked",
        "session.refresh_reused",
        "tenant.signed_up",
        "tenant.activated",
        "tenant.suspended",
        "tenant.reactivated",
        "tenant.declined",
        "tenant.deleted",
        "tenant.created",
        "member.invited",
        "invitation.revoked",
        "invitation.accepted",
        "member.role_changed",
        "member.removed",
        "plan.created",
        "plan.updated",
        "plan.deleted",
        "tenant.plan_changed",
        "pipeline.profile_created",  # PIP1
        "pipeline.profile_updated",
        "pipeline.profile_deleted",
        "pipeline.default_set",
        "pipeline.tenant_profile_set",
        "fair_use.overrides_updated",  # PIP3
        "fair_use.throttled",
        "fair_use.stopped",
        "fair_use.resumed",
        "fair_use.engine_spend_reached",
        "operator_workspace.created",
        "operator_workspace.suspend_refused",
        "operator_workspace.delete_refused",
        "cob.resync_requested",  # COB1
        "cob.action",  # COB5
        "cob.platform_doc_audience",  # COB9
        "cob.platform_doc_deleted",  # COB9
        "billing.provider_updated",  # BIL1
        "billing.pricing_updated",  # BIL2
        "billing.account_updated",  # BIL2
        "billing.ledger_entry",  # BIL3
        "billing.reconciliation_failed",  # BIL3
        # P1 payments
        "billing.payment_received",
        "billing.payment_mismatch",
        "billing.payment_link_created",
        "billing.refund_requested",
        "billing.refund_issued",
        "billing.refund_failed",
        "billing.chargeback",
        "billing.tax_details_updated",
        "billing.tax_profile_updated",
        "payments.settings_updated",
        # P2 invoices
        "billing.invoice_issued",
        "billing.credit_note_issued",
        # /P2 invoices
        # P3 legal, L5a demo (their strings, absent from this mirror)
        "legal.page_published",
        "legal.settings_updated",
        "demo.reset",
        "billing.add_on_created",  # BIL5
        "billing.add_on_updated",  # BIL5
        "billing.add_on_deleted",  # BIL5
        "billing.add_on_granted",  # BIL5
        "billing.grant_revoked",  # BIL5
        "billing.service_order_updated",  # P5 plans
        "billing.add_on_cancelled",  # P5 plans
        "billing.top_up_requested",  # BIL6
        "billing.paused",  # BIL4
        "billing.resumed",  # BIL4
        "api_key.created",
        "api_key.rotated",
        "api_key.updated",
        "api_key.revoked",
        "assistant.published",
        "assistant.rolled_back",
        "assistant.enabled_changed",
        "assistant.draft_discarded",
        "assistant.setup_applied",
        "lead.exported",
        "lead.deleted",
        "lead.withdrawn",
        "lead.retention_purged",
        "corpus_webhook.created",
        "corpus_webhook.deleted",
        "website.learning_started",  # BIZ1
        "website.settings_updated",
        "website.source_removed",
        "website.page_excluded",
        "website.page_included",
        "website.reread_requested",
        "website.learn_key_created",  # BIZ2
        "website.learn_key_revoked",
        "onboarding.website_started",  # BIZ4
        "assistant.avatar_uploaded",
        "value.settings_updated",  # TV4
        "billing.price_change_scheduled",  # TV3
        "billing.price_change_cancelled",  # TV3
        "billing.pending_change_scheduled",  # TV3
        "billing.pending_change_applied",  # TV3
        "billing.pending_change_cancelled",  # TV3
        "billing.change_agreed",  # TV3
        "billing.plan_change_applied",  # P6 plan choice
        "billing.plan_change_awaiting_payment",
        "billing.plan_change_scheduled",
        "landing.settings_updated",  # LP1 landing
        "landing.published",
        "landing.version_restored",
        "landing.asset_uploaded",
        "landing.asset_deleted",
        "landing.assistant_connected",
        "landing.assistant_disconnected",
        "landing.generated",
        "billing.spend_cap_set",  # TV3
        "billing.spend_cap_reached",  # TV3
        "support.request_created",  # TV3
        "support.request_status_changed",  # TV3
        "settings.price_sheet_link_rotated",  # TV3
        "settings.price_sheet_link_removed",  # TV3
        "whatsapp.reply_sent",  # TV6
        "whatsapp.connected",  # TV6
        "whatsapp.disconnected",  # TV6
        "whatsapp.settings_updated",  # TV6
        "whatsapp.token_updated",  # TV6
    } == AUDIT_ACTIONS
    assert all(len(a) <= 64 for a in AUDIT_ACTIONS)


def test_scrub_drops_secrets_at_any_depth() -> None:
    details = {
        "password": "hunter2",
        "note": "ok",
        "nested": {"Token": "t", "keep": 1, "deeper": [{"code": "123456", "api_key": "k"}]},
        "refresh_token": "rt_x",
        "secret": "s",
    }
    assert _scrub(details) == {"note": "ok", "nested": {"keep": 1, "deeper": [{}]}}


async def test_details_are_scrubbed_before_storing() -> None:
    row = await _row(None, details={"changed": {"password": "x", "name": "y"}})
    assert row.details == {"changed": {"name": "y"}}


def test_audit_log_has_no_foreign_keys() -> None:
    assert not AuditLog.__table__.foreign_keys
