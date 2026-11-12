"""Plans: the operator's catalogue of limits (accounts A6).

`/api/v1/admin/plans` (admin only): list, create, read, update, delete. The rules (one
default, no deleting a plan in use or the default, unique names) live in
`accounts/plans.py`; enforcement of the limits in `accounts/caps.py`. A tenant's plan is
set with `POST /api/v1/admin/tenants/{id}/plan` (or on activation), and read with
`GET /api/v1/admin/tenants/{id}/plan` or, by the tenant itself, `GET /api/v1/tenants/me/plan`
(both in `api/routes/tenants.py`, built by `plan_usage_response` below).
"""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts import caps, plans
from callobuzz_rag.api.deps import get_session, require_admin
from callobuzz_rag.api.schemas.plan import (
    CreatePlanRequest,
    PlanBase,
    PlanLimits,
    PlanListResponse,
    PlanOut,
    PlanUsage,
    PlanUsageResponse,
    UpdatePlanRequest,
)
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.tiers.admin import profile_labels  # PIP1 profile

logger = logging.getLogger(__name__)

admin_router = APIRouter(
    prefix="/api/v1/admin/plans", tags=["plans"], dependencies=[Depends(require_admin)]
)


def _out(plan: Plan, tenant_count: int, labels: dict[UUID, str] | None = None) -> PlanOut:
    base = PlanBase.model_validate(plan).model_dump()
    base["pipeline_profile_label"] = (labels or {}).get(plan.id)  # PIP1 profile
    base["fair_use_limits"] = plan.fair_use_limits  # PIP3 fair use
    base["fair_use_policy"] = plan.fair_use_policy  # PIP3 fair use
    return PlanOut(**base, tenant_count=tenant_count)


async def _one(session: AsyncSession, plan: Plan) -> PlanOut:
    counts = await plans.tenant_counts(session, [plan.id])
    labels = await profile_labels(session, [plan])  # PIP1 profile
    return _out(plan, counts.get(plan.id, 0), labels)


@admin_router.get("", response_model=PlanListResponse)
async def list_plans(
    include_inactive: bool = Query(True, description="false: only assignable plans"),
    session: AsyncSession = Depends(get_session),
) -> PlanListResponse:
    """Every plan, the default first, with how many tenants are on each."""
    items = await plans.list_plans(session, include_inactive=include_inactive)
    counts = await plans.tenant_counts(session, [p.id for p in items])
    labels = await profile_labels(session, items)  # PIP1 profile
    return PlanListResponse(
        items=[_out(p, counts.get(p.id, 0), labels) for p in items], count=len(items)
    )


@admin_router.post("", response_model=PlanOut, status_code=201)
async def create_plan(
    body: CreatePlanRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PlanOut:
    """Create a plan. Leave a limit out (or null) for unlimited. `is_default: true` makes
    it the default for new tenants; the previous default stops being one."""
    plan = await plans.create_plan(session, body.model_dump(), request=request)
    await session.commit()
    await session.refresh(plan)
    logger.info("Plan created: %s (%s)", plan.name, plan.id)
    return _out(plan, 0, await profile_labels(session, [plan]))  # PIP1 profile


@admin_router.get("/{plan_id}", response_model=PlanOut)
async def get_plan(plan_id: UUID, session: AsyncSession = Depends(get_session)) -> PlanOut:
    return await _one(session, await plans.get_plan(session, plan_id))


@admin_router.patch("/{plan_id}", response_model=PlanOut)
async def update_plan(
    plan_id: UUID,
    body: UpdatePlanRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PlanOut:
    """Change the fields sent. A limit sent as null becomes unlimited. Lowering a limit
    below what tenants use blocks new additions only. `is_active: false` stops the plan
    being assigned; the tenants on it keep it."""
    plan = await plans.get_plan(session, plan_id)
    changes: dict[str, Any] = {k: getattr(body, k) for k in body.model_fields_set}
    for flag in (
        "name",
        "is_default",
        "is_active",
        "count_cached_answers",
        "website_learning_enabled",  # BIZ3 plan
        "dashboard_features",
        "byok_allowed",  # P5 plans
        "self_service",  # P6 plan choice
    ):  # not nullable: null means "unchanged"
        if flag in changes and changes[flag] is None:
            del changes[flag]
    await plans.update_plan(session, plan, changes, request=request)
    await session.commit()
    await session.refresh(plan)
    return await _one(session, plan)


@admin_router.delete("/{plan_id}", status_code=204)
async def delete_plan(
    plan_id: UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    """Delete a plan no tenant uses (409 `plan_in_use` otherwise); never the default."""
    plan = await plans.get_plan(session, plan_id)
    await plans.delete_plan(session, plan, request=request)
    await session.commit()
    return Response(status_code=204)


async def plan_usage_response(session: AsyncSession, tenant: Tenant) -> PlanUsageResponse:
    """The tenant's plan, effective limits (override applied) and usage this month."""
    plan = await plans.get_plan(session, tenant.plan_id)
    limits = await caps.effective_limits(session, tenant.id)
    usage = await caps.usage_snapshot(session, tenant.id)
    return PlanUsageResponse(
        plan=PlanBase.model_validate(plan),
        limits=PlanLimits(
            max_knowledge_bases=limits.max_knowledge_bases,
            monthly_questions=limits.monthly_questions,
            max_documents=limits.max_documents,
            max_widget_keys=limits.max_widget_keys,
            max_upload_mb=limits.max_upload_mb,
            max_document_tokens=limits.max_document_tokens,
        ),
        usage=PlanUsage(
            month=usage.month.strftime("%Y-%m"),
            questions=usage.questions,
            knowledge_bases=usage.knowledge_bases,
            documents=usage.documents,
            widget_keys=usage.widget_keys,
        ),
        resets_at=caps.next_month_start(),
    )
