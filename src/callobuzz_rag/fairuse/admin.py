"""The deployer's side of fair use: overrides of one business and the lists (PIP3).

Rules live here, not in the routes: what an override may hold, that a downgrade target must
exist and must differ from the profile it replaces, and what is audited.
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.pipeline_profile import TenantPipelineSettings
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.fairuse.limits import (
    LIMIT_FIELDS,
    POLICY_FIELDS,
    FairUseLimits,
    FairUsePolicy,
    rule_error,
)

if TYPE_CHECKING:
    from fastapi import Request


def invalid(detail: str) -> APIError:
    return APIError(422, detail, "invalid_fair_use")


async def downgrade_target_count(session: AsyncSession, profile_id: uuid.UUID) -> int:
    """How many plans and businesses name `profile_id` as their downgrade profile."""
    wanted = str(profile_id)
    count = 0
    for (policy,) in (await session.execute(select(Plan.fair_use_policy))).all():
        if isinstance(policy, dict) and str(policy.get("downgrade_profile_id")) == wanted:
            count += 1
    for (overrides,) in (
        await session.execute(select(TenantPipelineSettings.policy_overrides))
    ).all():
        if isinstance(overrides, dict) and str(overrides.get("downgrade_profile_id")) == wanted:
            count += 1
    return count


async def _check_downgrade(session: AsyncSession, tenant: Tenant, downgrade_id: uuid.UUID) -> None:
    from callobuzz_rag.tiers import admin as tiers_admin

    try:
        await tiers_admin.get_profile(session, downgrade_id)
    except APIError as exc:
        raise invalid("The downgrade profile does not exist.") from exc
    plan_id, override_id = await tiers_admin.tenant_choice(session, tenant)
    own = override_id or plan_id
    if own is None:
        default = await tiers_admin.default_profile(session)
        own = default.id if default is not None else None
    if own == downgrade_id:
        raise invalid(
            "A business cannot downgrade to the profile it already answers with: pick another."
        )


async def set_overrides(
    session: AsyncSession,
    tenant: Tenant,
    *,
    limits: dict[str, int | None],
    policy: dict[str, Any],
    clear: list[str],
    request: "Request | None",
) -> None:
    """Change one business's own limits and policy (`clear` goes back to following the plan).

    A limit set to None is stored as null (unlimited for this business, whatever the plan says).
    Nothing is written, and nothing audited, when nothing changes.

    Raises:
        APIError 422 `invalid_fair_use` naming the rule: an unknown setting, a value out of
            range, a downgrade profile that does not exist or is the business's own.
    """
    unknown = [k for k in limits if k not in LIMIT_FIELDS]
    unknown += [k for k in policy if k not in POLICY_FIELDS]
    unknown += [k for k in clear if k not in LIMIT_FIELDS and k not in POLICY_FIELDS]
    if unknown:
        raise invalid(f"'{unknown[0]}' is not a fair-use setting.")
    try:
        for key, value in limits.items():
            if value is not None:
                FairUseLimits.model_validate({key: value})
        normalized = FairUsePolicy.model_validate(policy).model_dump(mode="json") if policy else {}
    except ValidationError as exc:
        raise invalid(rule_error(exc)) from exc
    target = policy.get("downgrade_profile_id")
    if target is not None:
        try:
            await _check_downgrade(session, tenant, uuid.UUID(str(target)))
        except ValueError as exc:
            raise invalid("The downgrade profile is not a valid id.") from exc

    row = (
        await session.execute(
            select(TenantPipelineSettings).where(TenantPipelineSettings.tenant_id == tenant.id)
        )
    ).scalar_one_or_none()
    stored_limits = dict(row.fair_use_overrides or {}) if row is not None else {}
    stored_policy = dict(row.policy_overrides or {}) if row is not None else {}
    changed: list[str] = []
    cleared: list[str] = []
    for key, value in limits.items():
        if key not in stored_limits or stored_limits[key] != value:
            stored_limits[key] = value
            changed.append(key)
    for key in policy:
        if key not in stored_policy or stored_policy[key] != normalized[key]:
            stored_policy[key] = normalized[key]
            changed.append(key)
    for key in clear:
        gone = stored_limits.pop(key, "-") != "-"
        gone = (stored_policy.pop(key, "-") != "-") or gone
        if gone:
            cleared.append(key)
    if not changed and not cleared:
        return
    if row is None:
        row = TenantPipelineSettings(tenant_id=tenant.id, state="ok")
        session.add(row)
    row.fair_use_overrides = stored_limits
    row.policy_overrides = stored_policy
    row.updated_at = datetime.now(UTC)
    await session.flush()
    await record_audit(
        session,
        request,
        "fair_use.overrides_updated",
        tenant_id=tenant.id,
        tenant_label=tenant.name,
        target_type="tenant",
        target_id=str(tenant.id),
        details={"changed": sorted(changed), "cleared": sorted(cleared)},
    )
