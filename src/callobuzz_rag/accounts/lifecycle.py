"""A tenant's lifecycle: sign-up, activate, suspend, reactivate, decline, delete (accounts A4).

    pending --activate--> active --suspend--> suspended --reactivate--> active
    pending --decline--> (deleted)          any --delete--> (deleted)

These functions are the only way a tenant's status changes, so every caller (the admin
routes, the `PUT /admin/tenants/{id}` mapping, a future payment webhook) gets the same
rules: each checks the transition (409 `invalid_status_transition`), checks the typed
tenant name where SV requires it (suspend and delete; 422 `confirm_mismatch`) and writes
its audit row. None of them commits: the caller commits, then sends the returned
`PendingEmails` (so a rolled-back change never sends mail) and, after a delete, cleans
up the vectors and files of the deleted knowledge bases.

The middleware enforces the status on every request (api/auth.py), so a suspension
takes effect at once for every key and session; suspension also revokes the sessions.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import delete, select

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.accounts.sessions import revoke_for_tenant
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.services.corpus_deletion import StoredCorpus, delete_corpus_rows
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

NOTE_MAX: Final = 1000


@dataclass
class PendingEmails:
    """`enqueue_email` calls to make once the change is committed."""

    items: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    def add(self, template: str, to: str, context: dict[str, Any]) -> None:
        self.items.append((template, to, context))

    def extend(self, other: "PendingEmails") -> None:
        self.items.extend(other.items)

    def send(self, *, strict: bool = False) -> None:
        """Queue every mail. Best-effort (logged) unless `strict`, which lets the first
        `EmailError` propagate (sign-up must tell the user; a notice must not fail an
        operator's committed action)."""
        from callobuzz_rag.accounts.email import dispatch
        from callobuzz_rag.core.exceptions import EmailError

        for template, to, context in self.items:
            if template in ("verify_code", "reset_code"):
                expires = dispatch.CODE_EXPIRES_S
            elif template == "invitation":
                expires = dispatch.INVITATION_EXPIRES_S
            else:
                expires = dispatch.NOTICE_EXPIRES_S
            try:
                dispatch.enqueue_email(template, to, context, expires_s=expires)
            except EmailError as exc:
                if strict:
                    raise
                logger.warning("Could not queue the %s e-mail: %s", template, type(exc).__name__)


@dataclass
class DeletedStorage:
    """What a deleted tenant left outside Postgres; clean it up after the commit."""

    corpora: list[StoredCorpus] = field(default_factory=list)


def _transition(tenant: Tenant, allowed_from: str, to: str) -> None:
    if tenant.status != allowed_from:
        extra: dict[str, Any] = {"from": tenant.status, "to": to}
        raise APIError(
            409,
            f"A {tenant.status} workspace cannot become {to}",
            "invalid_status_transition",
            **extra,
        )


def check_confirm_name(tenant: Tenant, confirm_name: str | None) -> None:
    """The typed name must equal the tenant's name (case-sensitive; outer spaces forgiven).

    Raises:
        APIError 422 `confirm_mismatch`.
    """
    if confirm_name is None or confirm_name.strip() != tenant.name:
        raise APIError(
            422,
            "Type the workspace's name exactly to confirm",
            "confirm_mismatch",
        )


def _settings(request: "Request | None") -> "Settings | None":
    if request is None:
        return None
    return getattr(request.app.state, "settings", None)


def _link(request: "Request | None", path: str) -> str:
    from callobuzz_rag.accounts.email.render import dashboard_link

    settings = _settings(request)
    base = settings.dashboard_url if settings is not None else "http://localhost:8090"
    return dashboard_link(base, path)


async def owner_emails(session: "AsyncSession", tenant_id: uuid.UUID) -> list[str]:
    rows = await session.execute(
        select(User.email)
        .join(TenantMember, TenantMember.user_id == User.id)
        .where(TenantMember.tenant_id == tenant_id, TenantMember.role == "owner")
    )
    return sorted({str(email) for email in rows.scalars().all()})


def _audit_label(tenant: Tenant) -> str:
    return tenant.name


# ------------------------------------------------------------------ sign-up


async def create_signup_tenant(
    session: "AsyncSession",
    *,
    user: User,
    business_name: str,
    signup_mode: str,
    request: "Request | None",
) -> tuple[Tenant, PendingEmails]:
    """The signer-up's new tenant with them as its owner: `active` in open mode, else
    `pending`. No tenant key is generated (the owner creates one when needed)."""
    from callobuzz_rag.accounts.plans import default_plan

    now = datetime.now(UTC)
    active = signup_mode == "open"
    tenant = Tenant(
        name=business_name,
        api_key_hash="",
        status="active" if active else "pending",
        activated_at=now if active else None,
        created_by_user_id=user.id,
        plan_id=(await default_plan(session)).id,
    )
    session.add(tenant)
    await session.flush()
    session.add(TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner"))
    await session.flush()
    await record_audit(
        session,
        request,
        "tenant.signed_up",
        tenant_id=tenant.id,
        tenant_label=_audit_label(tenant),
        target_type="tenant",
        target_id=str(tenant.id),
        details={"signup_mode": signup_mode, "status": tenant.status},
        actor=("user", user.id, user.email),
    )
    emails = PendingEmails()
    settings = _settings(request)
    superadmin = settings.superadmin_email if settings is not None else ""
    if not active and superadmin:
        from callobuzz_rag.accounts.passwords import normalize_email

        emails.add(
            "signup_notice",
            normalize_email(superadmin),
            {
                "tenant_name": tenant.name,
                "owner_name": user.name,
                "owner_email": user.email,
                "customers_url": _link(request, "/admin/customers?status=pending"),
            },
        )
    return tenant, emails


# ---------------------------------------------------------------- lifecycle


async def activate(
    session: "AsyncSession",
    tenant: Tenant,
    *,
    note: str | None,
    request: "Request | None",
    plan_id: uuid.UUID | None = None,
) -> tuple[Tenant, PendingEmails]:
    """pending -> active, with the operator's optional note, on `plan_id` (accounts A6;
    None keeps the plan it has, which is the default plan from sign-up unless the
    operator changed it). Mails the owners, naming the plan.

    Raises:
        APIError 409 `invalid_status_transition`, 404 `plan_not_found`, 409 `plan_inactive`.
    """
    from callobuzz_rag.accounts.plans import set_tenant_plan
    from callobuzz_rag.cob.system import refuse_system_tenant

    refuse_system_tenant(tenant)
    _transition(tenant, "pending", "active")
    # P6 plan choice: what the owner chose at sign-up. The operator picking that same plan (the
    # dialog preselects it) goes ahead with the owner's choice: a plan with a fee then waits for
    # the owner's payment, so the business is never charged on its own. Another plan is the
    # operator's word.
    requested = tenant.requested_plan_id
    tenant.requested_plan_id = None
    if plan_id is not None and plan_id == requested:
        plan_id = None
    elif plan_id is not None:
        requested = None
    # /P6 plan choice
    if plan_id is not None:
        await set_tenant_plan(session, tenant, plan_id, request=request)
    tenant.status = "active"
    tenant.activated_at = datetime.now(UTC)
    tenant.activation_note = note
    details: dict[str, Any] = {"plan_id": str(plan_id)} if plan_id is not None else {}
    if requested is not None:  # P6 plan choice
        details["requested_plan_id"] = str(requested)
    if note:
        details["note"] = note
    await record_audit(
        session,
        request,
        "tenant.activated",
        tenant_id=tenant.id,
        tenant_label=_audit_label(tenant),
        target_type="tenant",
        target_id=str(tenant.id),
        details=details,
    )
    # P6 plan choice: the owner's choice (a plan with a fee waits for the owner's payment)
    if requested is not None:
        from callobuzz_rag.billing.plan_change import apply_signup_choice

        try:
            async with session.begin_nested():
                await apply_signup_choice(session, tenant, requested, request=request)
        except APIError:
            logger.warning("Plan chosen at sign-up could not be applied", exc_info=True)
    # /P6 plan choice
    return tenant, await _activated_mail(session, tenant, request, reactivated=False)


async def _activated_mail(
    session: "AsyncSession", tenant: Tenant, request: "Request | None", *, reactivated: bool
) -> PendingEmails:
    from callobuzz_rag.db.models.plan import Plan

    plan = await session.get(Plan, tenant.plan_id) if tenant.plan_id else None
    emails = PendingEmails()
    for to in await owner_emails(session, tenant.id):
        emails.add(
            "activated",
            to,
            {
                "tenant_name": tenant.name,
                "plan_name": plan.name if plan is not None else None,
                "login_url": _link(request, "/login"),
                "reactivated": reactivated,
            },
        )
    return emails


async def suspend(
    session: "AsyncSession",
    tenant: Tenant,
    *,
    confirm_name: str | None,
    reason: str | None,
    request: "Request | None",
) -> tuple[Tenant, PendingEmails]:
    """active -> suspended. Needs the tenant's name typed. Revokes the sessions acting
    for it; every key stops at the middleware on its next request. Mails the owners.

    The operator's own workspace is never suspended (409 `operator_workspace_protected`,
    audited), whatever name is typed."""
    from callobuzz_rag.accounts.operator import refuse_suspend
    from callobuzz_rag.cob.system import refuse_system_tenant

    refuse_system_tenant(tenant)
    await refuse_suspend(session, tenant, request)
    check_confirm_name(tenant, confirm_name)
    _transition(tenant, "active", "suspended")
    tenant.status = "suspended"
    revoked = await revoke_for_tenant(session, tenant.id, "tenant_suspended")
    details: dict[str, Any] = {"sessions_revoked": revoked}
    if reason:
        details["reason"] = reason
    await record_audit(
        session,
        request,
        "tenant.suspended",
        tenant_id=tenant.id,
        tenant_label=_audit_label(tenant),
        target_type="tenant",
        target_id=str(tenant.id),
        details=details,
    )
    emails = PendingEmails()
    for to in await owner_emails(session, tenant.id):
        emails.add("suspended", to, {"tenant_name": tenant.name})
    return tenant, emails


async def reactivate(
    session: "AsyncSession", tenant: Tenant, *, request: "Request | None"
) -> tuple[Tenant, PendingEmails]:
    """suspended -> active. No typed name. Mails the owners."""
    from callobuzz_rag.cob.system import refuse_system_tenant

    refuse_system_tenant(tenant)
    _transition(tenant, "suspended", "active")
    tenant.status = "active"
    if tenant.activated_at is None:
        tenant.activated_at = datetime.now(UTC)
    await record_audit(
        session,
        request,
        "tenant.reactivated",
        tenant_id=tenant.id,
        tenant_label=_audit_label(tenant),
        target_type="tenant",
        target_id=str(tenant.id),
    )
    return tenant, await _activated_mail(session, tenant, request, reactivated=True)


async def decline(
    session: "AsyncSession", tenant: Tenant, *, reason: str | None, request: "Request | None"
) -> tuple[DeletedStorage, PendingEmails]:
    """A pending sign-up the operator rejects: deleted exactly like Delete (knowledge
    bases, then the tenant, then accounts left with no workspace), no typed name. The
    owners get a polite `declined` mail (the reason is only in the audit row)."""
    from callobuzz_rag.cob.system import refuse_system_tenant

    refuse_system_tenant(tenant)
    _transition(tenant, "pending", "declined")
    emails = PendingEmails()
    for to in await owner_emails(session, tenant.id):
        emails.add("declined", to, {"tenant_name": tenant.name})
    details: dict[str, Any] = {"reason": reason} if reason else {}
    storage = await _delete(session, tenant, request, action="tenant.declined", details=details)
    return storage, emails


async def delete_tenant(
    session: "AsyncSession",
    tenant: Tenant,
    *,
    confirm_name: str | None,
    request: "Request | None",
    confirm_operator_workspace: bool = False,
) -> DeletedStorage:
    """Delete the tenant and everything it owns, whatever its status. Needs the name
    typed. The caller commits, then runs `cleanup_corpus_storage` on the result.

    The operator's own workspace also needs `confirm_operator_workspace` (422
    `operator_confirmation_required`, audited); deleting it is remembered, so the
    bootstrap does not recreate it at the next start.
    """
    from callobuzz_rag.accounts.operator import require_delete_confirmation, set_workspace_removed
    from callobuzz_rag.cob.system import refuse_system_tenant

    refuse_system_tenant(tenant)
    check_confirm_name(tenant, confirm_name)
    await require_delete_confirmation(session, tenant, confirm_operator_workspace, request)
    is_operator = bool(tenant.is_operator)
    storage = await _delete(
        session,
        tenant,
        request,
        action="tenant.deleted",
        details={"operator_workspace": True} if is_operator else {},
    )
    if is_operator:
        await set_workspace_removed(session, True)
    return storage


async def _delete(
    session: "AsyncSession",
    tenant: Tenant,
    request: "Request | None",
    *,
    action: str,
    details: dict[str, Any],
) -> DeletedStorage:
    tenant_id = tenant.id
    await revoke_for_tenant(session, tenant_id, "tenant_deleted")
    member_ids = list(
        (
            await session.execute(
                select(TenantMember.user_id).where(TenantMember.tenant_id == tenant_id)
            )
        )
        .scalars()
        .all()
    )
    corpus_ids = list(
        (await session.execute(select(Corpus.id).where(Corpus.tenant_id == tenant_id)))
        .scalars()
        .all()
    )
    stored = await delete_corpus_rows(session, corpus_ids)
    await session.execute(delete(Tenant).where(Tenant.id == tenant_id))  # members cascade
    orphans = await _delete_orphan_users(session, member_ids, request)
    await record_audit(
        session,
        request,
        action,
        tenant_id=tenant_id,
        tenant_label=_audit_label(tenant),
        target_type="tenant",
        target_id=str(tenant_id),
        details={
            **details,
            "knowledge_bases_deleted": len(stored),
            "accounts_deleted": len(orphans),
        },
    )
    logger.info(
        "Tenant %s %s: %d knowledge bases, %d accounts removed",
        tenant_id,
        action.split(".")[1],
        len(stored),
        len(orphans),
    )
    return DeletedStorage(corpora=stored)


async def _delete_orphan_users(
    session: "AsyncSession", user_ids: Sequence[uuid.UUID], request: "Request | None"
) -> list[uuid.UUID]:
    """Delete the users among `user_ids` left with no membership (never the super
    admin). Their sessions and codes cascade."""
    if not user_ids:
        return []
    still_members = set(
        (
            await session.execute(
                select(TenantMember.user_id).where(TenantMember.user_id.in_(list(user_ids)))
            )
        )
        .scalars()
        .all()
    )
    candidates = [uid for uid in user_ids if uid not in still_members]
    if not candidates:
        return []
    users = (await session.execute(select(User).where(User.id.in_(candidates)))).scalars().all()
    orphans = [u.id for u in users if not _protected(u, request)]
    if orphans:
        await session.execute(delete(User).where(User.id.in_(orphans)))
    return orphans


def _protected(user: User, request: "Request | None") -> bool:
    """The super admin (flag or configured address) is never deleted with a tenant."""
    if user.is_superadmin:
        return True
    settings = _settings(request)
    if settings is None or not settings.superadmin_email:
        return False
    from callobuzz_rag.accounts.passwords import normalize_email

    return user.email == normalize_email(settings.superadmin_email)
