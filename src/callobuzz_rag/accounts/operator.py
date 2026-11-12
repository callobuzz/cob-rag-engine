"""The operator's own workspace (the house account) and its Internal plan.

A self-hosting operator is also their own first customer: their landing-page chatbot, their
pre-loaded knowledge base, demos. So the engine keeps one tenant for them, flagged
`tenants.is_operator` (at most one, a partial unique index):

- created by the bootstrap (`accounts/bootstrap.py`), idempotently at every start, named
  after `branding.product_name` (the stored value; "My workspace" when none is stored),
  `active`, on the built-in **Internal** plan (`plans.is_internal`: unlimited, locked, and
  assignable only to this workspace), with the super admin as its **owner**;
- never suspended (409 `operator_workspace_protected`);
- deleted only with the typed name AND `confirm_operator_workspace=true`
  (422 `operator_confirmation_required`); a deliberate delete is remembered
  (`engine_settings.operator_workspace_removed`) so the next start does not bring it back,
  and `bootstrap --restore-operator-workspace` clears that.

The super admin works inside it with `POST /api/v1/auth/switch-tenant` (see
`accounts/service.py`); no privilege is added: the super admin session is already the
admin scope.

None of these functions commits; each writes its audit row into the caller's session.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import delete, select

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.db.models.user import User

logger = logging.getLogger(__name__)

FALLBACK_NAME: Final = "My workspace"
INTERNAL_PLAN_NAME: Final = "Internal"
INTERNAL_PLAN_DESCRIPTION: Final = "The operator's own workspace: unlimited and free"
SETTINGS_ROW_ID: Final = 1
SYSTEM_ACTOR: Final = ("system", None, "operator workspace")


@dataclass
class EnsureResult:
    """What `ensure_operator_workspace` did."""

    tenant: Tenant | None
    created: bool = False
    owner_added: bool = False
    skipped_removed: bool = False


def workspace_name(stored_settings: Any) -> str:
    """The product name stored in the engine settings' branding, else "My workspace".

    Reads the raw stored JSON, not the validated model: an operator who never set a
    product name has the model's default ("cob-rag-engine"), which is not a name they chose.
    """
    branding = stored_settings.get("branding") if isinstance(stored_settings, dict) else None
    name = branding.get("product_name") if isinstance(branding, dict) else None
    if isinstance(name, str) and name.strip():
        return name.strip()[:255]
    return FALLBACK_NAME


async def get_operator_tenant(session: "AsyncSession") -> Tenant | None:
    return (
        await session.execute(select(Tenant).where(Tenant.is_operator.is_(True)))
    ).scalar_one_or_none()


async def internal_plan(session: "AsyncSession") -> Plan:
    """The Internal plan (the migration seeds it); recreated if someone removed it by hand."""
    plan = (
        await session.execute(select(Plan).where(Plan.is_internal.is_(True)))
    ).scalar_one_or_none()
    if plan is None:
        name = INTERNAL_PLAN_NAME
        if (await session.execute(select(Plan.id).where(Plan.name == name))).first() is not None:
            name = f"{INTERNAL_PLAN_NAME} (operator)"
        plan = Plan(
            name=name,
            description=INTERNAL_PLAN_DESCRIPTION,
            is_default=False,
            is_active=True,
            is_internal=True,
        )
        session.add(plan)
        await session.flush()
    return plan


async def workspace_removed(session: "AsyncSession") -> bool:
    row = await session.get(EngineSettingsRow, SETTINGS_ROW_ID)
    return bool(row is not None and row.operator_workspace_removed)


async def set_workspace_removed(session: "AsyncSession", removed: bool) -> None:
    row = await session.get(EngineSettingsRow, SETTINGS_ROW_ID)
    if row is None:  # the migration seeds it; recreate it if someone deleted it
        row = EngineSettingsRow(id=SETTINGS_ROW_ID, value={})
        session.add(row)
    row.operator_workspace_removed = removed


async def ensure_operator_workspace(
    session: "AsyncSession",
    *,
    owner: "User | None",
    restore: bool = False,
    request: "Request | None" = None,
) -> EnsureResult:
    """Make sure the operator workspace exists and `owner` (the super admin) is its owner.

    Idempotent: a second call changes nothing. With no `owner` (no super-admin address
    configured) the workspace has no members, so an admin-key operator can still use it.
    The previous super admin keeps their membership unless demoted. A workspace the
    operator deleted on purpose is not recreated unless `restore`.
    """
    removed = await workspace_removed(session)
    if removed and not restore:
        return EnsureResult(tenant=None, skipped_removed=True)
    if removed:
        await set_workspace_removed(session, False)

    tenant = await get_operator_tenant(session)
    created = False
    if tenant is None:
        row = await session.get(EngineSettingsRow, SETTINGS_ROW_ID)
        plan = await internal_plan(session)
        tenant = Tenant(
            name=workspace_name(row.value if row is not None else None),
            api_key_hash="",
            status="active",
            activated_at=datetime.now(UTC),
            created_by_user_id=owner.id if owner is not None else None,
            plan_id=plan.id,
            is_operator=True,
        )
        session.add(tenant)
        await session.flush()
        created = True
        await record_audit(
            session,
            request,
            "operator_workspace.created",
            tenant_id=tenant.id,
            tenant_label=tenant.name,
            target_type="tenant",
            target_id=str(tenant.id),
            details={
                "plan": plan.name,
                "owner": owner.email if owner is not None else None,
                "restored": removed,
            },
            actor=None if request is not None else SYSTEM_ACTOR,
        )
        logger.info("Operator workspace created: id=%s name=%s", tenant.id, tenant.name)

    owner_added = False
    if owner is not None:
        owner_added = await _ensure_owner(session, tenant.id, owner.id)
    return EnsureResult(tenant=tenant, created=created, owner_added=owner_added)


async def remove_memberships(session: "AsyncSession", user_ids: list[uuid.UUID]) -> int:
    """Take `user_ids` (demoted super admins) out of the operator workspace; returns how
    many memberships went. A demoted account must not keep owning the house account."""
    tenant = await get_operator_tenant(session)
    if tenant is None or not user_ids:
        return 0
    result = await session.execute(
        delete(TenantMember).where(
            TenantMember.tenant_id == tenant.id, TenantMember.user_id.in_(user_ids)
        )
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def _ensure_owner(session: "AsyncSession", tenant_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """`user_id` is an owner of the tenant; True when that changed something."""
    member = (
        await session.execute(
            select(TenantMember).where(
                TenantMember.tenant_id == tenant_id, TenantMember.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if member is None:
        session.add(TenantMember(tenant_id=tenant_id, user_id=user_id, role="owner"))
        await session.flush()
        return True
    if member.role != "owner":
        member.role = "owner"
        return True
    return False


# ------------------------------------------------------------ the protections


async def refuse_suspend(
    session: "AsyncSession", tenant: Tenant, request: "Request | None"
) -> None:
    """Raise 409 `operator_workspace_protected` for the operator workspace.

    The refusal is audited and committed here (the caller's request ends in an error, so
    nothing else would commit the row).
    """
    if not tenant.is_operator:
        return
    await record_audit(
        session,
        request,
        "operator_workspace.suspend_refused",
        tenant_id=tenant.id,
        tenant_label=tenant.name,
        target_type="tenant",
        target_id=str(tenant.id),
    )
    await session.commit()
    raise APIError(
        409,
        "This is your own workspace; it cannot be suspended",
        "operator_workspace_protected",
    )


async def require_delete_confirmation(
    session: "AsyncSession",
    tenant: Tenant,
    confirm_operator_workspace: bool,
    request: "Request | None",
) -> None:
    """Raise 422 `operator_confirmation_required` when the operator workspace is being
    deleted without the explicit flag (audited and committed, like `refuse_suspend`)."""
    if not tenant.is_operator or confirm_operator_workspace:
        return
    await record_audit(
        session,
        request,
        "operator_workspace.delete_refused",
        tenant_id=tenant.id,
        tenant_label=tenant.name,
        target_type="tenant",
        target_id=str(tenant.id),
    )
    await session.commit()
    raise APIError(
        422,
        "This is your own workspace: also send confirm_operator_workspace=true to delete it",
        "operator_confirmation_required",
    )


async def restore_workspace(session: "AsyncSession", *, request: "Request") -> Tenant:
    """ "Recreate my workspace": clear the deliberate-delete mark and make the workspace, the
    same way the bootstrap does (`ensure_operator_workspace(restore=True)`).

    The super admin who asked owns it (a super-admin session); an admin-key call gives it to
    the configured super admin (`RAG_SUPERADMIN_EMAIL`), or, with none, leaves it without
    members. The caller commits. Audited as `operator_workspace.created` with `restored`.

    Raises:
        APIError 409 `operator_workspace_exists`: there is nothing to recreate.
    """
    if await get_operator_tenant(session) is not None:
        raise APIError(
            409,
            "Your own workspace already exists; there is nothing to recreate",
            "operator_workspace_exists",
        )
    owner = await _restoring_owner(session, request)
    result = await ensure_operator_workspace(session, owner=owner, restore=True, request=request)
    if result.tenant is None:  # restore=True always yields the workspace
        raise RuntimeError("the operator workspace was not created")
    return result.tenant


async def _restoring_owner(session: "AsyncSession", request: "Request") -> "User | None":
    from callobuzz_rag.db.models.user import User

    caller = getattr(request.state, "user", None)
    if caller is not None and getattr(caller, "is_superadmin", False):
        return caller  # type: ignore[no-any-return]
    email = getattr(getattr(request.app.state, "settings", None), "superadmin_email", "") or ""
    if not email:
        return None
    from callobuzz_rag.accounts.passwords import normalize_email

    return (
        await session.execute(select(User).where(User.email == normalize_email(email)))
    ).scalar_one_or_none()
