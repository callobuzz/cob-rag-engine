"""Tenant endpoints: admin-only management and lifecycle, and a tenant's own record.

`/api/v1/admin/tenants` is the operator's CRUD plus the lifecycle (accounts A4):
activate a pending sign-up, suspend (the tenant's name typed), reactivate, decline a
pending sign-up, and hard delete (the name typed). The lifecycle rules live in
`accounts/lifecycle.py`; these routes commit, then send the mail and clean up storage.
`/api/v1/tenants/me` lets a tenant key or workspace sign-in read its own record and
rotate its own key. A tenant's plan (accounts A6) is set with `POST .../{id}/plan` or on
activation, and read with `GET .../{id}/plan` or `GET /api/v1/tenants/me/plan`.
"""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts import caps, lifecycle, plans
from callobuzz_rag.accounts.api_keys import (
    DEFAULT_KEY_NAME,
    create_key,
    rotate_default_key,
    tenant_owner,
)
from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.accounts.caps import Limits
from callobuzz_rag.api.deps import (
    get_authenticated_tenant,
    get_session,
    require_admin,
    require_owner,
)
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.routes.plans import plan_usage_response
from callobuzz_rag.api.schemas.delete_preview import DeletePreviewResponse
from callobuzz_rag.api.schemas.plan import PlanUsageResponse, SetTenantPlanRequest
from callobuzz_rag.api.schemas.tenant import (
    ActivateTenantRequest,
    AdminTenantResponse,
    CreateTenantRequest,
    CreateTenantResponse,
    DeclineTenantRequest,
    RotateTenantKeyResponse,
    SuspendTenantRequest,
    TenantListResponse,
    TenantResponse,
    TenantStatus,
    UpdateTenantRequest,
)
from callobuzz_rag.billing.limits import active_grant_sum, apply_limit_adjustments
from callobuzz_rag.cob.system import refuse_system_tenant
from callobuzz_rag.db.models.api_key import ApiKey
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.tenant_question_count import TenantQuestionCount
from callobuzz_rag.db.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/tenants", tags=["tenants"])
self_router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Tenant not found")


def _plan_column(column: Any) -> Any:
    return select(column).where(Plan.id == Tenant.plan_id).correlate(Tenant).scalar_subquery()


def _tenant_columns() -> list[Any]:
    """Tenant plus corpus count, key count and its plan's name and knowledge-base limit."""
    corpus_count = (
        select(func.count(Corpus.id))
        .where(Corpus.tenant_id == Tenant.id)
        .correlate(Tenant)
        .scalar_subquery()
    )
    key_count = (
        select(func.count(ApiKey.id))
        .where(ApiKey.tenant_id == Tenant.id)
        .correlate(Tenant)
        .scalar_subquery()
    )
    overrides = (
        select(BillingAccount.limit_overrides)
        .where(BillingAccount.tenant_id == Tenant.id)
        .correlate(Tenant)
        .scalar_subquery()
    )
    now = datetime.now(UTC)
    return [
        Tenant,
        corpus_count.label("corpus_count"),
        key_count.label("key_count"),
        _plan_column(Plan.name).label("plan_name"),
        _plan_column(Plan.max_knowledge_bases).label("plan_max_kbs"),
        # what the billing account and the add-ons in force change (`caps.effective_limits`)
        overrides.label("limit_overrides"),
        active_grant_sum(Tenant.id, "max_knowledge_bases", now).label("grant_max_kbs"),
        active_grant_sum(Tenant.id, "monthly_questions", now).label("grant_questions"),
    ]


def _admin_columns() -> list[Any]:
    """`_tenant_columns` plus owner e-mails, member count, the plan's monthly cap and this
    month's answered questions, as correlated subqueries."""
    owners = (
        select(func.array_agg(User.email))
        .select_from(TenantMember)
        .join(User, User.id == TenantMember.user_id)
        .where(TenantMember.tenant_id == Tenant.id, TenantMember.role == "owner")
        .correlate(Tenant)
        .scalar_subquery()
    )
    members = (
        select(func.count())
        .select_from(TenantMember)
        .where(TenantMember.tenant_id == Tenant.id)
        .correlate(Tenant)
        .scalar_subquery()
    )
    questions = (
        select(TenantQuestionCount.questions)
        .where(
            TenantQuestionCount.tenant_id == Tenant.id,
            TenantQuestionCount.month == caps.current_month(),
        )
        .correlate(Tenant)
        .scalar_subquery()
    )
    return [
        *_tenant_columns(),
        owners.label("owner_emails"),
        members.label("member_count"),
        _plan_column(Plan.monthly_questions).label("plan_monthly_questions"),
        questions.label("questions_this_month"),
        # P6 plan choice: the plan chosen at sign-up (approval mode)
        select(Plan.name)
        .where(Plan.id == Tenant.requested_plan_id)
        .correlate(Tenant)
        .scalar_subquery()
        .label("requested_plan_name"),
    ]


def _effective_limit(row: Any, field: str, plan_value: int | None, grant: int) -> int | None:
    """One limit as `caps.effective_limits` reads it: the plan's value, the account's own
    override, plus the add-ons in force. None = unlimited."""
    fields: dict[str, Any] = {field: plan_value}
    base = Limits(**fields)
    overrides = dict(row.limit_overrides or {})
    adjusted = apply_limit_adjustments(base, overrides, {field: int(grant or 0)})
    value: int | None = getattr(adjusted, field)
    return value


def _effective_max_corpora(row: Any) -> int | None:
    tenant: Tenant = row.Tenant
    plan_value = tenant.max_corpora if tenant.max_corpora is not None else row.plan_max_kbs
    return _effective_limit(row, "max_knowledge_bases", plan_value, row.grant_max_kbs)


def _tenant_fields(row: Any) -> dict[str, Any]:
    tenant: Tenant = row.Tenant
    return {
        "id": tenant.id,
        "name": tenant.name,
        "description": tenant.description,
        "corpus_count": row.corpus_count or 0,
        "max_corpora": tenant.max_corpora,
        "effective_max_corpora": _effective_max_corpora(row),
        "plan_id": tenant.plan_id,
        "plan_name": row.plan_name,
        "status": tenant.status,
        "is_active": tenant.is_active,
        "is_operator": bool(tenant.is_operator),
        "has_api_key": (row.key_count or 0) > 0,
        "activated_at": tenant.activated_at,
        "created_at": tenant.created_at,
    }


def _admin_response(row: Any) -> AdminTenantResponse:
    tenant: Tenant = row.Tenant
    return AdminTenantResponse(
        **_tenant_fields(row),
        activation_note=tenant.activation_note,
        created_by_user_id=tenant.created_by_user_id,
        owner_emails=sorted(row.owner_emails or []),
        member_count=row.member_count or 0,
        monthly_questions=_effective_limit(
            row, "monthly_questions", row.plan_monthly_questions, row.grant_questions
        ),
        questions_this_month=row.questions_this_month or 0,
        requested_plan_id=tenant.requested_plan_id,  # P6 plan choice
        requested_plan_name=row.requested_plan_name if tenant.requested_plan_id else None,
    )


async def _admin_tenant(session: AsyncSession, tenant_id: UUID) -> AdminTenantResponse:
    row = (
        await session.execute(select(*_admin_columns()).where(Tenant.id == tenant_id))
    ).one_or_none()
    if row is None:
        raise _not_found()
    return _admin_response(row)


async def _load(session: AsyncSession, tenant_id: UUID, *, protect: bool = False) -> Tenant:
    """The tenant, 404 when absent. `protect=True` (every write) refuses COB's own workspace."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise _not_found()
    if protect:
        refuse_system_tenant(tenant)
    return tenant


@router.post("", response_model=CreateTenantResponse, status_code=201)
async def create_tenant(
    body: CreateTenantRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> CreateTenantResponse:
    """Create an active tenant with a dedicated API key.

    - Generates a unique API key (returned once — store securely)
    - On `plan_id` (an active plan) or else the default plan; `max_corpora` overrides the
      plan's knowledge-base limit (omit it to follow the plan)
    - Requires admin authentication
    """
    plan = (
        await plans.assignable_plan(session, body.plan_id)
        if body.plan_id is not None
        else await plans.default_plan(session)
    )
    tenant = Tenant(
        name=body.name,
        description=body.description,
        max_corpora=body.max_corpora,
        plan_id=plan.id,
        status="active",
        activated_at=datetime.now(UTC),
    )
    session.add(tenant)
    await session.flush()  # Get the generated ID

    # The customer's first key: its "Default key", full permission. More named keys are made
    # through /admin/tenants/{id}/keys or the customer's own /tenants/me/keys.
    _, api_key = await create_key(
        session,
        tenant_owner(tenant),
        name=DEFAULT_KEY_NAME,
        permission="full",
        request=request,
        is_default=True,
        audit=False,
    )
    await record_audit(
        session,
        request,
        "tenant.created",
        tenant_id=tenant.id,
        tenant_label=tenant.name,
        target_type="tenant",
        target_id=str(tenant.id),
        details={"plan": plan.name},
    )

    await session.commit()
    await session.refresh(tenant)

    logger.info("Tenant created: id=%s name=%s", tenant.id, body.name)

    return CreateTenantResponse(
        id=tenant.id,
        name=tenant.name,
        description=tenant.description,
        api_key=api_key,
        max_corpora=tenant.max_corpora,
        plan_id=tenant.plan_id,
        status="active",
        is_active=tenant.is_active,
        created_at=tenant.created_at,
    )


@router.get("", response_model=TenantListResponse)
async def list_tenants(
    status: TenantStatus | None = Query(None, description="Only tenants in this status"),
    search: str | None = Query(
        None, max_length=255, description="Case-insensitive part of the name"
    ),
    is_operator: bool | None = Query(
        None,
        description="false: customers only (without the operator's own workspace); "
        "true: only the operator workspace",
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> TenantListResponse:
    """List tenants, newest first, with owners, member and corpus counts. Admin only."""
    filters: list[Any] = [Tenant.is_system.is_(False)]  # COB's built-in workspace is never listed
    if status is not None:
        filters.append(Tenant.status == status)
    if is_operator is not None:
        filters.append(Tenant.is_operator.is_(is_operator))
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append(Tenant.name.ilike(f"%{escaped}%", escape="\\"))

    total = (await session.execute(select(func.count(Tenant.id)).where(*filters))).scalar() or 0
    rows = (
        await session.execute(
            select(*_admin_columns())
            .where(*filters)
            .order_by(Tenant.created_at.desc(), Tenant.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    items = [_admin_response(row) for row in rows]
    return TenantListResponse(
        items=items, count=len(items), total=int(total), offset=offset, limit=limit
    )


@router.get("/{tenant_id}", response_model=AdminTenantResponse)
async def get_tenant(
    tenant_id: UUID,
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> AdminTenantResponse:
    """Tenant detail with owners, member and corpus counts. Admin only."""
    return await _admin_tenant(session, tenant_id)


@router.put("/{tenant_id}", response_model=AdminTenantResponse)
async def update_tenant(
    tenant_id: UUID,
    body: UpdateTenantRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> AdminTenantResponse:
    """Update name, description, max_corpora; `is_active` suspends or reactivates.

    `max_corpora` overrides the plan's knowledge-base limit; `null` removes the override.
    Lowering it below the tenant's count blocks new knowledge bases only.

    `is_active: false` suspends an active tenant and needs `confirm_name` (its current
    name) in the same body; `true` reactivates a suspended one. A value the tenant
    already has changes nothing. A pending tenant is activated with `POST .../activate`.
    The operator's own workspace cannot be suspended (409 `operator_workspace_protected`).
    """
    tenant = await _load(session, tenant_id, protect=True)
    emails = lifecycle.PendingEmails()
    if body.is_active is False and tenant.status != "suspended":
        _, emails = await lifecycle.suspend(
            session, tenant, confirm_name=body.confirm_name or "", reason=None, request=request
        )
    elif body.is_active is True and tenant.status != "active":
        _, emails = await lifecycle.reactivate(session, tenant, request=request)

    if body.name is not None:
        tenant.name = body.name
    if body.description is not None:
        tenant.description = body.description
    if "max_corpora" in body.model_fields_set:  # an explicit null follows the plan again
        tenant.max_corpora = body.max_corpora

    await session.commit()
    emails.send()
    logger.info("Tenant updated: id=%s", tenant_id)
    return await _admin_tenant(session, tenant_id)


@router.get("/{tenant_id}/delete-preview", response_model=DeletePreviewResponse)
async def preview_delete_tenant(
    tenant_id: UUID,
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> DeletePreviewResponse:
    """What deleting this customer would remove, and the name to confirm with. Changes
    nothing."""
    from callobuzz_rag.api.services.delete_guard import tenant_counts

    tenant = await _load(session, tenant_id)
    return DeletePreviewResponse(
        kind="customer",
        id=tenant.id,
        name=tenant.name,
        confirm_name=tenant.name,
        counts=await tenant_counts(session, tenant.id),
    )


@router.delete("/{tenant_id}", status_code=204)
async def delete_tenant(
    tenant_id: UUID,
    request: Request,
    confirm_name: str | None = Query(
        None,
        max_length=255,
        description="The tenant's name, typed exactly (case-sensitive). Required with an API "
        "key and with a session alike; GET .../delete-preview says what would be removed.",
    ),
    confirm_operator_workspace: bool = Query(
        False,
        description="Also required to delete the operator's own workspace "
        "(422 operator_confirmation_required without it)",
    ),
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> Response:
    """Delete a tenant and everything it owns: its knowledge bases (with their vectors
    and files), members, and the accounts that belonged only to it. Any status.

    Requires `?confirm_name=<the tenant's name>`; a call without it is a 422 and
    deletes nothing (this route used to deactivate: use `POST .../suspend` for that).
    The operator's own workspace (`is_operator`) also needs
    `?confirm_operator_workspace=true`; once deleted it is not recreated at the next start.
    """
    if confirm_name is None:
        raise APIError(
            422,
            "Pass confirm_name with the customer's exact name "
            "(GET .../delete-preview says what would be removed)",
            "confirmation_required",
        )
    tenant = await _load(session, tenant_id)
    storage = await lifecycle.delete_tenant(
        session,
        tenant,
        confirm_name=confirm_name,
        request=request,
        confirm_operator_workspace=confirm_operator_workspace,
    )
    await session.commit()
    from callobuzz_rag.api.services.corpus_deletion import cleanup_corpus_storage

    await cleanup_corpus_storage(request.app.state, storage.corpora)
    return Response(status_code=204)


@router.post("/{tenant_id}/activate", response_model=AdminTenantResponse)
async def activate_tenant(
    tenant_id: UUID,
    request: Request,
    body: ActivateTenantRequest | None = None,
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> AdminTenantResponse:
    """Activate a pending sign-up on a plan (`plan_id`; default: the plan it has, which
    is the default plan unless changed), optionally with a note (e.g. how they paid).
    The owners get a "you're in" e-mail naming the plan."""
    tenant = await _load(session, tenant_id)
    note = body.note if body is not None else None
    plan_id = body.plan_id if body is not None else None
    _, emails = await lifecycle.activate(
        session, tenant, note=note, request=request, plan_id=plan_id
    )
    await session.commit()
    emails.send()
    return await _admin_tenant(session, tenant_id)


@router.post("/{tenant_id}/suspend", response_model=AdminTenantResponse)
async def suspend_tenant(
    tenant_id: UUID,
    body: SuspendTenantRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> AdminTenantResponse:
    """Suspend an active tenant: every API key, widget and sign-in of it stops at once.

    `confirm_name` must be the tenant's name exactly (422 `confirm_mismatch`). The
    operator's own workspace (`is_operator`) is never suspended: 409
    `operator_workspace_protected`.
    """
    tenant = await _load(session, tenant_id)
    _, emails = await lifecycle.suspend(
        session, tenant, confirm_name=body.confirm_name, reason=body.reason, request=request
    )
    await session.commit()
    emails.send()
    return await _admin_tenant(session, tenant_id)


@router.post("/{tenant_id}/reactivate", response_model=AdminTenantResponse)
async def reactivate_tenant(
    tenant_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> AdminTenantResponse:
    """Reactivate a suspended tenant. Its keys work again on their next request."""
    tenant = await _load(session, tenant_id)
    _, emails = await lifecycle.reactivate(session, tenant, request=request)
    await session.commit()
    emails.send()
    return await _admin_tenant(session, tenant_id)


@router.post("/{tenant_id}/decline", status_code=204)
async def decline_tenant(
    tenant_id: UUID,
    request: Request,
    body: DeclineTenantRequest | None = None,
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> Response:
    """Decline a pending sign-up: the tenant and its sign-up account are removed and
    the owner gets a polite e-mail (the reason stays in the audit log)."""
    tenant = await _load(session, tenant_id)
    storage, emails = await lifecycle.decline(
        session, tenant, reason=body.reason if body is not None else None, request=request
    )
    await session.commit()
    emails.send()
    from callobuzz_rag.api.services.corpus_deletion import cleanup_corpus_storage

    await cleanup_corpus_storage(request.app.state, storage.corpora)
    return Response(status_code=204)


@router.post("/{tenant_id}/rotate-key", response_model=RotateTenantKeyResponse)
async def rotate_tenant_key(
    tenant_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> RotateTenantKeyResponse:
    """Replace the tenant's default key. The old one is immediately invalidated.

    Returns the new key (shown only once — store securely). Other named keys
    (`/admin/tenants/{id}/keys`) are untouched; with none, this issues the first one.
    Requires admin authentication.
    """
    tenant = await _load(session, tenant_id, protect=True)
    new_key = await rotate_default_key(session, tenant_owner(tenant), request=request)

    await session.commit()

    logger.info("API key rotated for tenant %s", tenant_id)

    return RotateTenantKeyResponse(api_key=new_key)


@router.post("/{tenant_id}/plan", response_model=AdminTenantResponse)
async def set_tenant_plan(
    tenant_id: UUID,
    body: SetTenantPlanRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> AdminTenantResponse:
    """Move a tenant to another active plan (any status). Takes effect on the next
    request; a lower limit blocks new additions only and deletes nothing."""
    tenant = await _load(session, tenant_id, protect=True)
    # TV3 notice: a move to a plan that costs this business more waits for the notice period,
    # unless the business asked for it (then the reason is recorded)
    waiting = await _hold_plan_move(session, request, tenant, body)
    if waiting is not None:
        held, ctx = waiting
        await session.commit()
        await _tell_pending_owners(session, request, ctx, held)
        out = await _admin_tenant(session, tenant_id)
        out.plan_change_scheduled_for = held.at
        return out
    await _drop_stale_plan_move(session, request, tenant)
    # /TV3 notice
    await plans.set_tenant_plan(session, tenant, body.plan_id, request=request)
    await session.commit()
    return await _admin_tenant(session, tenant_id)


# TV3 notice
async def _hold_plan_move(
    session: AsyncSession, request: Request, tenant: Tenant, body: SetTenantPlanRequest
) -> tuple[Any, Any] | None:
    """Check the move as `set_tenant_plan` would (plan exists, is assignable, currency fits),
    then decide whether it must wait. None: move it now; else the held change and the billing
    context it was decided on."""
    from datetime import UTC, datetime

    from callobuzz_rag.accounts.settings_store import read_engine_settings_row
    from callobuzz_rag.billing import accounts as billing_accounts
    from callobuzz_rag.billing import notice
    from callobuzz_rag.billing.config import billing_enabled
    from callobuzz_rag.billing.pricing import check_plan_currency

    if not billing_enabled() or tenant.plan_id == body.plan_id:
        return None
    plan = await plans.assignable_plan(
        session, body.plan_id, tenant_is_operator=bool(tenant.is_operator)
    )
    await check_plan_currency(session, tenant.id, plan.id)
    ctx = await billing_accounts.load_context(session, tenant.id)
    engine = (await read_engine_settings_row(session))[0]
    held = await notice.hold_plan_move(
        session,
        ctx,
        plan,
        agreed=body.business_agreed,
        reason=body.reason,
        notice_days=engine.trust.price_change_notice_days,
        now=datetime.now(UTC),
        request=request,
    )
    return (held, ctx) if held is not None else None


async def _drop_stale_plan_move(session: AsyncSession, request: Request, tenant: Tenant) -> None:
    """A plan move that applies now is the deployer's latest decision: a held move to another
    plan must not override it at the renewal, so it is cancelled (audited). Nothing pending, or
    billing off: nothing to do."""
    from types import SimpleNamespace

    from sqlalchemy import select

    from callobuzz_rag.billing import notice
    from callobuzz_rag.billing.config import billing_enabled
    from callobuzz_rag.db.models.billing_account import BillingAccount

    if not billing_enabled():
        return
    account = (
        await session.execute(
            select(BillingAccount)
            .where(BillingAccount.tenant_id == tenant.id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if account is not None and (account.pending_change or {}).get("plan_id"):
        await notice.cancel_pending(
            session, SimpleNamespace(account=account, tenant=tenant), request
        )


async def _tell_pending_owners(
    session: AsyncSession, request: Request, ctx: Any, held: Any
) -> None:
    """After the commit: tell the owners a move to a dearer plan is coming. Best effort."""
    try:
        from callobuzz_rag.accounts.email.dispatch import enqueue_email
        from callobuzz_rag.accounts.settings_store import read_engine_settings_row
        from callobuzz_rag.billing import notice

        engine = (await read_engine_settings_row(session))[0]
        settings = getattr(request.app.state, "settings", None)
        base = settings.dashboard_url if settings is not None else "http://localhost:8090"
        await notice.notify_pending(
            session,
            ctx,
            held,
            enqueue=enqueue_email,
            product_name=engine.branding.product_name,
            dashboard_url=base,
        )
    except Exception:
        logger.warning("Pending plan change notice was not sent", exc_info=True)


# /TV3 notice


@router.get("/{tenant_id}/plan", response_model=PlanUsageResponse)
async def get_tenant_plan(
    tenant_id: UUID,
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> PlanUsageResponse:
    """A tenant's plan, effective limits and usage this month. Admin only."""
    tenant = await _load(session, tenant_id)
    return await plan_usage_response(session, tenant)


async def _tenant_response(session: AsyncSession, tenant_id: UUID) -> TenantResponse:
    row = (
        await session.execute(select(*_tenant_columns()).where(Tenant.id == tenant_id))
    ).one_or_none()
    if row is None:
        raise _not_found()
    return TenantResponse(**_tenant_fields(row))


@self_router.get("/me", response_model=TenantResponse)
async def get_own_tenant(
    tenant: Tenant = Depends(get_authenticated_tenant),
    session: AsyncSession = Depends(get_session),
) -> TenantResponse:
    """The calling tenant's own record: name, status, limits, corpus count.

    Also answers a signed-in user of a pending or suspended workspace (its status page).
    """
    return await _tenant_response(session, tenant.id)


@self_router.get("/me/plan", response_model=PlanUsageResponse)
async def get_own_plan(
    tenant: Tenant = Depends(get_authenticated_tenant),
    session: AsyncSession = Depends(get_session),
) -> PlanUsageResponse:
    """The calling tenant's plan, its effective limits and usage this month ("412 of
    1,000 questions this month"). Owners and members alike."""
    managed = await _load(session, tenant.id)
    return await plan_usage_response(session, managed)


@self_router.post("/me/rotate-key", response_model=RotateTenantKeyResponse)
async def rotate_own_tenant_key(
    request: Request,
    _owner: None = Depends(require_owner),
    tenant: Tenant = Depends(get_authenticated_tenant),
    session: AsyncSession = Depends(get_session),
) -> RotateTenantKeyResponse:
    """Replace the calling tenant's default key. The new key is shown once; the old one stops
    now. Owners only. Other named keys (`/tenants/me/keys`) are untouched.

    The auth middleware remembers a verified key only together with the hash it
    verified against, so replacing the hash ends the old key on its next request.
    """
    managed = await session.get(Tenant, tenant.id)
    if managed is None:
        raise _not_found()
    new_key = await rotate_default_key(session, tenant_owner(managed), request=request)
    await session.commit()
    logger.info("Tenant %s rotated its own API key", managed.id)
    return RotateTenantKeyResponse(api_key=new_key)


operator_router = APIRouter(prefix="/api/v1/admin/operator-workspace", tags=["tenants"])


@operator_router.post("/restore", response_model=AdminTenantResponse, status_code=201)
async def restore_operator_workspace(
    request: Request,
    session: AsyncSession = Depends(get_session),
    _admin: None = Depends(require_admin),
) -> AdminTenantResponse:
    """ "Recreate my workspace": bring back the operator's own workspace after it was deleted
    on purpose (a deleted one stays deleted across restarts; this clears that and recreates
    it, as `bootstrap --restore-operator-workspace` does). Admin key or super-admin session.
    409 `operator_workspace_exists` when there is nothing to recreate."""
    from callobuzz_rag.accounts.operator import restore_workspace

    tenant = await restore_workspace(session, request=request)
    await session.commit()
    logger.info("Operator workspace restored: id=%s", tenant.id)
    return await _admin_tenant(session, tenant.id)
