"""`record_audit`: add one `audit_log` row to the caller's session.

The row is added, never committed here: it commits or rolls back with the change it
describes. The actor comes from `request.state` (what the auth middleware set) unless
given; a call without a request is the system (CLI, beat). `details` never carries
secrets: keys named like passwords, codes or tokens are dropped at any depth.
"""

import uuid
from typing import TYPE_CHECKING, Any, Final

from callobuzz_rag.db.models.audit_log import AuditLog

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

AUDIT_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        # A7
        "settings.updated",
        # P3 legal: a legal page version published (slug, version, note; never the text) and the
        # grievance officer / footer settings changed
        "legal.page_published",
        "legal.settings_updated",
        # /P3 legal
        # LP1 landing: the public website's settings, a published version, a restored version
        # (never the text), and (with the uploads) a file added or removed
        "landing.settings_updated",
        "landing.published",
        "landing.version_restored",
        "landing.asset_uploaded",
        "landing.asset_deleted",
        # /LP1 landing
        # LP2 landing: a conversation written from a knowledge base (ids and language only)
        "landing.generated",
        # /LP2 landing
        # LP4 landing: the website's "Ask it yourself" connected to or disconnected from an assistant
        "landing.assistant_connected",
        "landing.assistant_disconnected",
        # /LP4 landing
        # L5a demo: the demo server put itself back to the seed (system; counts only, no content)
        "demo.reset",
        # /L5a demo
        # TV6: a WhatsApp number connected, its token replaced, its settings changed or the
        # number disconnected (never the token), and an owner's dashboard reply (never the text)
        "whatsapp.connected",
        "whatsapp.disconnected",
        "whatsapp.settings_updated",
        "whatsapp.token_updated",
        "whatsapp.reply_sent",
        # TV4: an owner saved the values the value report uses
        "value.settings_updated",
        # BIL1: the super admin changed a provider's balance settings (never a key)
        "billing.provider_updated",
        # BIL2: a plan's pricing was saved (a new version)
        "billing.pricing_updated",
        # BIL2: a customer's billing terms, limits, currency or pause override changed
        "billing.account_updated",
        # BIL3: a manual top-up, correction or refund (automated postings are ledger-only, D7)
        "billing.ledger_entry",
        # BIL3: the daily billing reconciliation found a mismatch (system actor)
        "billing.reconciliation_failed",
        # BIL4: a workspace's answers paused for balance (or the operator forced it) and resumed
        "billing.paused",
        "billing.resumed",
        # BIL5: the add-on catalogue changed; an add-on was given to a customer or ended
        "billing.add_on_created",
        "billing.add_on_updated",
        "billing.add_on_deleted",
        "billing.add_on_granted",
        "billing.grant_revoked",
        # P5 plans: a service order moved along; an owner stopped a monthly add-on renewing
        "billing.service_order_updated",
        "billing.add_on_cancelled",
        # /P5 plans
        # BIL6: a workspace owner asked the operator for a top-up
        "billing.top_up_requested",
        # TV3: a price change was announced for a plan, or called off
        "billing.price_change_scheduled",
        "billing.price_change_cancelled",
        # TV3: a raise for one workspace waits for its notice, then applies; or the business
        # agreed to it at once (with the reason), or it was called off
        "billing.pending_change_scheduled",
        "billing.pending_change_applied",
        "billing.pending_change_cancelled",
        "billing.change_agreed",
        # P6 plan choice: an owner moved up a plan (at once, or once the payment arrived), asked
        # for a move that waits for a payment, or scheduled a move down for the period end
        "billing.plan_change_applied",
        "billing.plan_change_awaiting_payment",
        "billing.plan_change_scheduled",
        # /P6 plan choice
        # TV3: the owner set or removed the monthly spending cap; usage reached it (system)
        "billing.spend_cap_set",
        "billing.spend_cap_reached",
        # TV3: a business opened a support request, or its status changed (never the text)
        "support.request_created",
        "support.request_status_changed",
        # TV3: the price list's public link was made new (the old one stops) or removed
        "settings.price_sheet_link_rotated",
        "settings.price_sheet_link_removed",
        # A3
        "superadmin.bootstrapped",
        "user.password_reset",
        "user.password_changed",
        "user.login_locked",
        "session.refresh_reused",
        # A4
        "tenant.signed_up",
        "tenant.activated",
        "tenant.suspended",
        "tenant.reactivated",
        "tenant.declined",
        "tenant.deleted",
        "tenant.created",
        # A5
        "member.invited",
        "invitation.revoked",
        "invitation.accepted",
        "member.role_changed",
        "member.removed",
        # A6
        "plan.created",
        "plan.updated",
        "plan.deleted",
        "tenant.plan_changed",
        # PIP1: pipeline profiles (how a business's questions are answered)
        "pipeline.profile_created",
        "pipeline.profile_updated",
        "pipeline.profile_deleted",
        "pipeline.default_set",
        "pipeline.tenant_profile_set",
        # PIP3 fair use: overrides edited by the deployer; a business throttled, stopped or
        # resumed (system actor, once per transition); the engine's daily AI spend reached
        "fair_use.overrides_updated",
        "fair_use.throttled",
        "fair_use.stopped",
        "fair_use.resumed",
        "fair_use.engine_spend_reached",
        # The operator's own workspace (its delete is a `tenant.deleted` row with
        # `operator_workspace: true`)
        "operator_workspace.created",
        "operator_workspace.suspend_refused",
        "operator_workspace.delete_refused",
        # Named API keys (`accounts/api_keys.py`); the details never carry a key
        "api_key.created",
        "api_key.rotated",
        "api_key.updated",
        "api_key.revoked",
        # Conversation layer C2: assistant configuration
        "assistant.published",
        "assistant.rolled_back",
        "assistant.enabled_changed",
        "assistant.draft_discarded",
        # Conversation layer C8: guided setup
        "assistant.setup_applied",
        # Conversation layer C4: leads and knowledge-base webhooks
        "lead.exported",
        "lead.deleted",
        "lead.withdrawn",
        "lead.retention_purged",
        "corpus_webhook.created",
        "corpus_webhook.deleted",
        # COB1: the operator asks COB to re-read its docs
        "cob.resync_requested",
        # COB5: a person confirmed an action card (the route it ran writes its own row too)
        "cob.action",
        # COB9 platform docs (start)
        "cob.platform_doc_audience",
        "cob.platform_doc_deleted",
        # COB9 platform docs (end)
        # BIZ1 learning: a business's website (automated reads are in website_runs, not here)
        "website.learning_started",
        "website.settings_updated",
        "website.source_removed",
        "website.page_excluded",
        "website.page_included",
        "website.reread_requested",
        # /BIZ1 learning
        # BIZ2 freshness: the push link (the key itself is never recorded)
        "website.learn_key_created",
        "website.learn_key_revoked",
        # /BIZ2 freshness
        # BIZ4 onboarding
        "assistant.avatar_uploaded",
        "onboarding.website_started",
        # /BIZ4 onboarding
        # P1 payments: the seller's and a workspace's tax details (more actions join as the
        # payment code that writes them does: money received, refunds, a payment link, ...)
        "payments.settings_updated",
        "billing.tax_profile_updated",
        "billing.tax_details_updated",
        "billing.payment_received",
        "billing.payment_mismatch",
        "billing.chargeback",
        "billing.payment_link_created",
        "billing.refund_requested",
        "billing.refund_issued",
        "billing.refund_failed",
        # /P1 payments
        # P2 invoices
        "billing.invoice_issued",
        "billing.credit_note_issued",
        # /P2 invoices
    }
)

_SECRET_KEYS: Final[frozenset[str]] = frozenset(
    {"password", "code", "token", "refresh_token", "api_key", "secret"}
)

Actor = tuple[str, uuid.UUID | None, str]


def _scrub(value: Any) -> Any:
    """`value` without any dict key naming a secret, at any depth."""
    if isinstance(value, dict):
        return {
            k: _scrub(v)
            for k, v in value.items()
            if not (isinstance(k, str) and k.lower() in _SECRET_KEYS)
        }
    if isinstance(value, list | tuple):
        return [_scrub(v) for v in value]
    return value


def actor_from_request(request: "Request | None") -> Actor:
    """(actor_kind, actor_user_id, actor_label) for whoever made `request`."""
    if request is None:
        return ("system", None, "system")
    state = request.state
    user = getattr(state, "user", None)
    if user is not None:
        return ("user", user.id, str(user.email))
    if getattr(state, "is_admin", False):
        return ("admin_key", None, "admin key")
    from callobuzz_rag.api.permissions import KeyInfo

    info = getattr(state, "api_key", None)
    named = f" ({info.name})" if isinstance(info, KeyInfo) else ""
    tenant = getattr(state, "tenant", None)
    if tenant is not None:
        return ("tenant_key", None, f"tenant key {tenant.name}{named}")
    corpus = getattr(state, "corpus", None)
    if corpus is not None:
        return ("corpus_key", None, f"corpus key {corpus.name}{named}")
    return ("system", None, "system")


def _caller_address(request: "Request | None") -> str | None:
    if request is None:
        return None
    from callobuzz_rag.widget.security import request_client_ip

    return request_client_ip(request)[:64]


async def record_audit(
    session: "AsyncSession",
    request: "Request | None",
    action: str,
    *,
    tenant_id: uuid.UUID | None = None,
    tenant_label: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict[str, Any] | None = None,
    actor: Actor | None = None,
) -> None:
    """Add an audit row for `action` to `session` (the caller commits)."""
    assert action in AUDIT_ACTIONS, f"unknown audit action {action!r}"
    kind, user_id, label = actor if actor is not None else actor_from_request(request)
    session.add(
        AuditLog(
            actor_kind=kind,
            actor_user_id=user_id,
            actor_label=label[:320],
            action=action,
            tenant_id=tenant_id,
            tenant_label=tenant_label[:255] if tenant_label is not None else None,
            target_type=target_type,
            target_id=target_id,
            details=_scrub(details or {}),
            ip=_caller_address(request),
        )
    )


# OS2 retired
# Recorded by the removed licensing system. Never recorded again; the audit read API still lists
# and filters on them.
RETIRED_AUDIT_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "license.activated",
        "license.activation_failed",
        "license.deactivated",
        "license.state_changed",
    }
)
# /OS2 retired
