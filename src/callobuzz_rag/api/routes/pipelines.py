"""Pipeline profiles: the deployer decides how a business's questions are answered (PIP1).

`/api/v1/admin/pipelines` (admin only): the profile catalogue, the live state of the answer
lanes, and the profile a single business is given. Which profile a plan picks is a field of
the plan (`pipeline_profile_id`, `api/routes/plans.py`). The rules (one default, no deleting a
profile in use, unique names) live in `tiers/admin.py`; resolution at answer time in
`tiers/profiles.py`.

The static paths (`/lanes`, `/tenants/{id}`) are declared before `/{profile_id}`.
"""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, require_admin
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.pipelines import (
    CloneProfileRequest,
    CreateProfileRequest,
    LanesOut,
    PoolOut,
    ProfileListResponse,
    ProfileOut,
    ResolvedOut,
    SetTenantPipelineRequest,
    TenantPipelineOut,
    UpdateProfileRequest,
)
from callobuzz_rag.db.models.pipeline_profile import PipelineProfile
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.tiers import admin
from callobuzz_rag.tiers.lanes import get_lanes, lane_configs
from callobuzz_rag.tiers.profiles import effective

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/pipelines", tags=["pipelines"], dependencies=[Depends(require_admin)]
)


def _tenant_not_found() -> APIError:
    return APIError(404, "That business does not exist.", "tenant_not_found")


async def _outs(
    session: AsyncSession, request: Request, rows: list[PipelineProfile]
) -> list[ProfileOut]:
    settings = request.app.state.settings
    plan_counts, tenant_counts = await admin.usage_counts(session, [r.id for r in rows])
    out: list[ProfileOut] = []
    for row in rows:
        view = effective(row, "plan", settings=settings)
        out.append(
            ProfileOut(
                id=row.id,
                name=row.name,
                label=row.label,
                description=row.description,
                builtin_key=row.builtin_key,
                is_default=row.is_default,
                config=view.config,
                resolved=ResolvedOut(**admin.resolved_view(view.config, settings)),
                plan_count=plan_counts.get(row.id, 0),
                tenant_count=tenant_counts.get(row.id, 0),
                available=view.available,
                created_at=row.created_at,
                updated_at=row.updated_at,
                updated_by=row.updated_by,
            )
        )
    return out


async def _one(session: AsyncSession, request: Request, row: PipelineProfile) -> ProfileOut:
    return (await _outs(session, request, [row]))[0]


@router.get("", response_model=ProfileListResponse)
async def list_profiles(
    request: Request, session: AsyncSession = Depends(get_session)
) -> ProfileListResponse:
    """Every pipeline profile, the default first, with how many plans and businesses use each
    and what its blank fields mean right now."""
    rows = await admin.list_profiles(session)
    default = next((r for r in rows if r.is_default), None)
    return ProfileListResponse(
        items=await _outs(session, request, rows), default_id=default.id if default else None
    )


def _warn_model_missing(request: Request, row: Any) -> None:
    """Warn (never refuse) when a local profile names a model the local server does not hold:
    the profile then shows as not available and its businesses are paused until it is pulled."""
    from callobuzz_rag.llm.local import local_models, same_model
    from callobuzz_rag.tiers.profiles import parse_config

    settings = request.app.state.settings
    config = parse_config(row.config, name=row.name)
    wanted = config.answer_model
    if config.answer_route != "local" or wanted is None:
        return
    held = local_models(settings)
    if held and not any(same_model(m, wanted) for m in held):
        logger.warning(
            "Profile %s names local model %s, which is not downloaded on the local server",
            row.name,
            wanted,
        )


@router.post("", response_model=ProfileOut, status_code=201)
async def create_profile(
    body: CreateProfileRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ProfileOut:
    """Create a profile. 422 `invalid_profile` names the rule when the settings do not fit
    together; 409 `profile_name_taken` when the name is in use."""
    row = await admin.create_profile(session, body.model_dump(), request=request)
    await session.commit()
    await session.refresh(row)
    logger.info("Pipeline profile created: %s (%s)", row.name, row.id)
    _warn_model_missing(request, row)
    return await _one(session, request, row)


@router.get("/lanes", response_model=LanesOut)
async def get_lanes_state(request: Request) -> LanesOut:
    """The answer lanes right now: slots and how many are taken, by priority. Best effort:
    when Redis cannot be read the pools show as idle."""
    settings = request.app.state.settings
    configs = lane_configs(settings)
    snapshot = await get_lanes(request.app).snapshot()
    return LanesOut(
        enabled=configs["engine"].slots > 0,
        pools={name: PoolOut(**state) for name, state in snapshot.items()},
    )


async def _tenant(session: AsyncSession, tenant_id: UUID) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise _tenant_not_found()
    return tenant


async def _tenant_out(session: AsyncSession, request: Request, tenant: Tenant) -> TenantPipelineOut:
    settings = request.app.state.settings
    plan_id, override_id = await admin.tenant_choice(session, tenant)
    view = await admin.effective_for_tenant(session, tenant, settings=settings)
    row = await admin.get_profile(session, view.id) if view.id is not None else None
    if row is None:
        raise admin.profile_not_found()
    return TenantPipelineOut(
        plan_profile_id=plan_id,
        override_profile_id=override_id,
        effective=await _one(session, request, row),
    )


@router.get("/tenants/{tenant_id}", response_model=TenantPipelineOut)
async def get_tenant_pipeline(
    tenant_id: UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> TenantPipelineOut:
    """The profile a business answers with, and where it comes from (its own override, its
    plan's, or the default)."""
    return await _tenant_out(session, request, await _tenant(session, tenant_id))


@router.put("/tenants/{tenant_id}", response_model=TenantPipelineOut)
async def set_tenant_pipeline(
    tenant_id: UUID,
    body: SetTenantPipelineRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TenantPipelineOut:
    """Give a business a profile of its own, or send `profile_id: null` to go back to its
    plan's."""
    tenant = await _tenant(session, tenant_id)
    await admin.set_tenant_profile(session, tenant, body.profile_id, request=request)
    await session.commit()
    return await _tenant_out(session, request, tenant)


@router.get("/{profile_id}", response_model=ProfileOut)
async def get_profile(
    profile_id: UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> ProfileOut:
    return await _one(session, request, await admin.get_profile(session, profile_id))


@router.patch("/{profile_id}", response_model=ProfileOut)
async def update_profile(
    profile_id: UUID,
    body: UpdateProfileRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ProfileOut:
    """Change the fields sent; `config` replaces the whole config."""
    row = await admin.get_profile(session, profile_id)
    fields = {k: getattr(body, k) for k in body.model_fields_set}
    await admin.update_profile(session, row, fields, request=request)
    await session.commit()
    await session.refresh(row)
    _warn_model_missing(request, row)
    return await _one(session, request, row)


@router.post("/{profile_id}/clone", response_model=ProfileOut, status_code=201)
async def clone_profile(
    profile_id: UUID,
    body: CloneProfileRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ProfileOut:
    """A copy of a profile under a new name: never the default, never a built-in."""
    source = await admin.get_profile(session, profile_id)
    row = await admin.clone_profile(
        session, source, name=body.name, label=body.label, request=request
    )
    await session.commit()
    await session.refresh(row)
    return await _one(session, request, row)


@router.post("/{profile_id}/default", response_model=ProfileOut)
async def make_default(
    profile_id: UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> ProfileOut:
    """Make this the default profile: plans that pick none answer with it. The old default
    stops being one in the same step."""
    row = await admin.get_profile(session, profile_id)
    await admin.make_default(session, row, request=request)
    await session.commit()
    await session.refresh(row)
    return await _one(session, request, row)


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(
    profile_id: UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    """Delete a profile no plan or business uses (409 `profile_in_use` otherwise); never the
    default (409 `profile_is_default`)."""
    row = await admin.get_profile(session, profile_id)
    await admin.delete_profile(session, row, request=request)
    await session.commit()
    return Response(status_code=204)
