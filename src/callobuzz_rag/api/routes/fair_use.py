"""Fair use (PIP3): what the deployer sets for a business, and what an owner may read.

Admin routes (`/api/v1/admin/fair-use`, admin only): who is near a limit, one business's
limits, policy and usage, and its own overrides of its plan. Owner route (`/api/v1/fair-use`):
a workspace principal reads where it stands, read-only; AI spend is shown as a share only (the
customer never sees the deployer's AI cost). What a plan's limits are is a field of the plan
(`api/routes/plans.py`); the decision itself runs in `fairuse/gate.py`.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, require_admin, resolve_scope
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.fair_use import (
    BreachOut,
    DecisionsToday,
    FairUseAdminView,
    FairUseListResponse,
    FairUseOverridesRequest,
    FairUseOwnerView,
    FairUseRow,
    MetricUsageOut,
    OwnerMetric,
)
from callobuzz_rag.db.models.pipeline_profile import TenantPipelineSettings
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.fairuse import admin as fair_admin
from callobuzz_rag.fairuse import views
from callobuzz_rag.fairuse.limits import effective_fair_use
from callobuzz_rag.tiers import admin as tiers_admin

logger = logging.getLogger(__name__)

admin_router = APIRouter(
    prefix="/api/v1/admin/fair-use", tags=["fair-use"], dependencies=[Depends(require_admin)]
)
owner_router = APIRouter(prefix="/api/v1/fair-use", tags=["fair-use"])


async def _tenant(session: AsyncSession, tenant_id: UUID) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise APIError(404, "That business does not exist.", "tenant_not_found")
    return tenant


async def _plan_name(session: AsyncSession, tenant: Tenant) -> str:
    plan = (
        await session.execute(select(Plan).where(Plan.id == tenant.plan_id))
    ).scalar_one_or_none()
    return plan.name if plan is not None else ""


async def _admin_view(session: AsyncSession, request: Request, tenant: Tenant) -> FairUseAdminView:
    effective = await effective_fair_use(session, tenant.id)
    rows, _ = await views.metric_rows(session, request.app, tenant.id, effective)
    settings_row = await views.settings_row(session, tenant.id)
    return FairUseAdminView(
        tenant_id=tenant.id,
        name=tenant.name,
        plan=await _plan_name(session, tenant),
        limits=effective.limits,
        policy=effective.policy,
        overridden=sorted(effective.overridden),
        usage=[
            MetricUsageOut(
                metric=r.metric,
                label=r.label,
                used=r.used,
                limit=r.limit,
                pct=r.pct,
                level=r.level,
                resets_at=r.resets_at,
            )
            for r in rows
        ],
        state=views.live_state(effective.state, rows),
        state_since=settings_row.state_since if settings_row is not None else None,
        decisions_today=DecisionsToday(**await views.decisions_today(session, tenant.id)),
        exempt=effective.exempt,
    )


@admin_router.get("", response_model=FairUseListResponse)
async def list_fair_use(
    request: Request,
    q: str | None = Query(None, max_length=100, description="Part of the business's name"),
    state: str | None = Query(None, pattern="^(ok|near|throttled|stopped)$"),
    ids: list[UUID] | None = Query(
        None,
        max_length=100,
        description="Only these businesses (the customers page asks for the rows it shows)",
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> FairUseListResponse:
    """Businesses with their fair-use state (`ok`, `near`, `throttled`, `stopped`), the most
    worrying first. The state is the one the engine last stored (`state` and the order use it),
    and the most serious limit is worked out live for every business on the page: one whose
    uploads, storage or website reads are used shows `near` with that limit named, though no
    answer was ever downgraded."""
    rows = (
        await session.execute(
            select(Tenant.id, Tenant.name, Plan.name, Tenant.is_system)
            .select_from(Tenant)
            .join(Plan, Plan.id == Tenant.plan_id)
        )
    ).all()
    states = {
        tid: st
        for tid, st in (
            await session.execute(
                select(TenantPipelineSettings.tenant_id, TenantPipelineSettings.state)
            )
        ).all()
    }
    rank = {"stopped": 0, "throttled": 1, "near": 2, "ok": 3}
    items = [
        (tid, name, plan, states.get(tid, "ok"))
        for tid, name, plan, is_system in rows
        if not is_system and (not q or q.lower() in str(name).lower())
    ]
    if ids is not None:
        wanted = set(ids)
        items = [i for i in items if i[0] in wanted]
    if state is not None:
        items = [i for i in items if i[3] == state]
    items.sort(key=lambda i: (rank.get(i[3], 3), str(i[1]).lower()))
    page = items[offset : offset + limit]
    out: list[FairUseRow] = []
    now = datetime.now(UTC)
    for tid, name, plan, st in page:
        tenant = await session.get(Tenant, tid)
        label = ""
        exempt = False
        worst: BreachOut | None = None
        if tenant is not None:
            profile = await tiers_admin.effective_for_tenant(
                session, tenant, settings=request.app.state.settings
            )
            label = profile.label
            effective = await effective_fair_use(session, tid)
            exempt = effective.exempt
            if not exempt:
                metric_rows, breaches = await views.metric_rows(
                    session, request.app, tid, effective, now=now
                )
                st = views.live_state(st, metric_rows)
                found = views.worst_breach(breaches)
                if found is not None:
                    worst = BreachOut(
                        metric=found.metric,
                        level=found.level,
                        limit=found.limit,
                        used=found.used,
                        resets_at=found.resets_at,
                    )
        out.append(
            FairUseRow(
                tenant_id=tid,
                name=str(name),
                plan=str(plan),
                profile_label=label,
                state=st,
                exempt=exempt,
                worst=worst,
            )
        )
    return FairUseListResponse(items=out, total=len(items))


@admin_router.get("/tenants/{tenant_id}", response_model=FairUseAdminView)
async def get_fair_use(
    tenant_id: UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> FairUseAdminView:
    """One business's effective limits and policy (plan, then its own overrides), how much of
    each is used now, its state and what the engine decided for it today."""
    return await _admin_view(session, request, await _tenant(session, tenant_id))


@admin_router.put("/tenants/{tenant_id}", response_model=FairUseAdminView)
async def set_fair_use(
    tenant_id: UUID,
    body: FairUseOverridesRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> FairUseAdminView:
    """Give a business limits or a policy of its own. A limit sent as null is unlimited for
    this business; `clear` lists settings that go back to following the plan. 422
    `invalid_fair_use` names the rule that is broken."""
    tenant = await _tenant(session, tenant_id)
    await fair_admin.set_overrides(
        session,
        tenant,
        limits=body.limits,
        policy=body.policy,
        clear=body.clear,
        request=request,
    )
    await session.commit()
    return await _admin_view(session, request, tenant)


@owner_router.get("", response_model=FairUseOwnerView)
async def fair_use(
    request: Request, session: AsyncSession = Depends(get_session)
) -> FairUseOwnerView:
    """Where this workspace stands against its limits: how fast answers run, its state and
    each limit with the share used. Read-only; members see the same. AI usage appears as a
    share only."""
    scope = resolve_scope(request)
    if scope.corpus_id is not None:
        raise APIError(
            403, "Fair use belongs to a workspace, not to one knowledge base.", "scope_forbidden"
        )
    if scope.is_admin or scope.tenant_id is None:
        raise APIError(403, "There is no workspace here: use the admin pages.", "no_workspace")
    tenant = await _tenant(session, scope.tenant_id)
    effective = await effective_fair_use(session, tenant.id)
    rows, _ = await views.metric_rows(session, request.app, tenant.id, effective)
    from callobuzz_rag.tiers import profiles as tier_profiles

    profile = await tier_profiles.plan_profile(
        session, tenant.id, settings=request.app.state.settings
    )
    answer_speed = profile.label
    # The slower profile a throttled or balance-less workspace falls back to.
    target = await tier_profiles.load_profile(
        session, effective.policy.downgrade_profile_id, settings=request.app.state.settings
    )
    if effective.state == "throttled" and target is not None:
        answer_speed = target.label
    # With an empty balance the workspace is only slowed down when that profile can answer (D14:
    # an unavailable local model pauses instead), so the dashboard says what really happens.
    billing_block = effective.policy.on_billing_block
    if billing_block == "downgrade_profile" and (
        target is None or not getattr(target, "available", True)
    ):
        billing_block = "pause_handoff"
    # A plan whose own profile answers on the local model pauses while that model is not ready.
    answers_paused = (
        "local_unavailable"
        if profile.config.answer_route == "local" and not profile.available
        else None
    )
    return FairUseOwnerView(
        answer_speed=answer_speed,
        state=views.live_state(effective.state, rows),
        answers_paused=answers_paused,
        billing_block=billing_block,
        metrics=[
            OwnerMetric(
                metric=r.metric,
                label=r.label,
                used=None if r.is_spend else r.used,
                limit=None if r.is_spend else r.limit,
                pct=r.pct,
                level=r.level,
                resets_at=r.resets_at,
            )
            for r in rows
        ],
    )
