"""Plans: the operator's catalogue of limits, and a tenant's plan (accounts A6).

Rules (every caller, including a future payment webhook, goes through these):

- Exactly one plan is the default. Making a plan the default unsets the old one in the
  same transaction; the default cannot be un-defaulted, deactivated or deleted
  (409 `plan_is_default`) — make another plan the default first.
- A plan tenants use cannot be deleted (409 `plan_in_use`); deactivate it instead: it can
  no longer be assigned (409 `plan_inactive`), and the tenants on it keep it.
- Names are unique (409 `plan_name_taken`).
- The built-in **Internal** plan (`is_internal`, the operator workspace's) is locked: it
  cannot be edited, deactivated or deleted (409 `plan_internal_locked`) and only the
  operator workspace may be put on it (409 `plan_not_assignable`). "Pilot" is an ordinary
  plan: editable and deletable like any other.

None of these functions commits; each writes its audit row into the caller's session.
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.core.exceptions import PlanConfigurationError
from callobuzz_rag.db.models.plan import LIMIT_FIELDS, Plan
from callobuzz_rag.db.models.tenant import Tenant

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

EDITABLE_FIELDS = (
    "name",
    "description",
    *LIMIT_FIELDS,
    "count_cached_answers",
    "is_default",
    "is_active",
    "website_learning_enabled",  # BIZ3 plan
    "website_pages_max",
    "dashboard_features",
    "pipeline_profile_id",  # PIP1 profile
    "fair_use_limits",  # PIP3 fair use
    "fair_use_policy",  # PIP3 fair use
    "channels",  # TV6 channel
    "byok_allowed",  # P5 plans
    "self_service",  # P6 plan choice
)


# BIZ3 plan
def _flags(value: Any) -> dict[str, bool]:
    """The dashboard flags as stored: the full set, defaults filled in."""
    from callobuzz_rag.api.features import full_features

    raw = value.model_dump() if hasattr(value, "model_dump") else value
    return full_features(raw if isinstance(raw, dict) else None)


# /BIZ3 plan


# TV6 channel
def _channels(value: Any) -> dict[str, Any]:
    """The channels policy as stored: the full set, defaults filled in."""
    from callobuzz_rag.channels.policy import normalize_channels

    return normalize_channels(value)


# /TV6 channel


async def _profile_exists(session: "AsyncSession", profile_id: uuid.UUID | None) -> None:
    """PIP1 profile: a plan may only pick a profile that exists (404 `profile_not_found`);
    None means the default profile."""
    if profile_id is not None:
        from callobuzz_rag.tiers.admin import get_profile

        await get_profile(session, profile_id)


# PIP3 fair use
async def _fair_use_values(
    session: "AsyncSession",
    fields: dict[str, Any],
    *,
    profile_id: uuid.UUID | None,
) -> dict[str, Any]:
    """The plan's fair-use fields as stored, validated: only the limits that are set and only
    the policy values that differ from the defaults. 422 `invalid_fair_use` names the rule (a
    downgrade profile that does not exist, or the plan's own profile)."""
    from pydantic import ValidationError

    from callobuzz_rag.fairuse.limits import (
        FairUseLimits,
        FairUsePolicy,
        normalize_limits,
        normalize_policy,
        rule_error,
    )

    out: dict[str, Any] = {}
    try:
        if "fair_use_limits" in fields:
            raw = fields["fair_use_limits"]
            out["fair_use_limits"] = normalize_limits(
                FairUseLimits.model_validate(raw) if isinstance(raw, dict) else raw or {}
            )
        if "fair_use_policy" in fields:
            raw = fields["fair_use_policy"]
            out["fair_use_policy"] = normalize_policy(
                FairUsePolicy.model_validate(raw) if isinstance(raw, dict) else raw or {}
            )
    except ValidationError as exc:
        raise APIError(422, rule_error(exc), "invalid_fair_use") from exc
    downgrade = (out.get("fair_use_policy") or {}).get("downgrade_profile_id")
    if downgrade is not None:
        from callobuzz_rag.tiers.admin import default_profile, get_profile

        try:
            await get_profile(session, uuid.UUID(str(downgrade)))
        except APIError as exc:
            raise APIError(
                422, "The downgrade profile does not exist.", "invalid_fair_use"
            ) from exc
        own = profile_id
        if own is None:
            default = await default_profile(session)
            own = default.id if default is not None else None
        if own is not None and str(own) == str(downgrade):
            raise APIError(
                422,
                "A plan cannot downgrade to its own profile: pick a cheaper one.",
                "invalid_fair_use",
            )
    return out


# /PIP3 fair use


def _not_found() -> APIError:
    return APIError(404, "Plan not found", "plan_not_found")


def _name_taken(name: str) -> APIError:
    return APIError(409, f"A plan named {name!r} already exists", "plan_name_taken")


def _summary(plan: Plan) -> dict[str, Any]:
    return {"id": str(plan.id), "name": plan.name}


async def default_plan(session: "AsyncSession") -> Plan:
    """The default plan (the migration seeds one; the rules above keep exactly one)."""
    plan = (
        await session.execute(select(Plan).where(Plan.is_default.is_(True)))
    ).scalar_one_or_none()
    if plan is None:  # only possible if someone edited the table by hand
        raise PlanConfigurationError("No default plan: mark one plan as the default")
    return plan


async def get_plan(session: "AsyncSession", plan_id: uuid.UUID) -> Plan:
    """Raises: APIError 404 `plan_not_found`."""
    plan = await session.get(Plan, plan_id)
    if plan is None:
        raise _not_found()
    return plan


async def assignable_plan(
    session: "AsyncSession", plan_id: uuid.UUID, *, tenant_is_operator: bool = False
) -> Plan:
    """A plan a tenant may be put on (`tenant_is_operator`: the operator workspace, the
    only tenant that may be on the Internal plan).

    Raises:
        APIError 404 `plan_not_found`, 409 `plan_inactive`, 409 `plan_not_assignable`.
    """
    plan = await get_plan(session, plan_id)
    if plan.is_internal and not tenant_is_operator:
        raise APIError(
            409,
            "The Internal plan is for the operator's own workspace only",
            "plan_not_assignable",
        )
    if not plan.is_active:
        raise APIError(409, "This plan is deactivated and cannot be assigned", "plan_inactive")
    return plan


def _internal_locked() -> APIError:
    return APIError(
        409,
        "The Internal plan is built in and cannot be changed or deleted",
        "plan_internal_locked",
    )


async def tenant_counts(session: "AsyncSession", plan_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not plan_ids:
        return {}
    rows = await session.execute(
        select(Tenant.plan_id, func.count(Tenant.id))
        .where(Tenant.plan_id.in_(plan_ids), Tenant.is_system.is_(False))
        .group_by(Tenant.plan_id)
    )
    return {plan_id: int(count) for plan_id, count in rows.all()}


async def list_plans(session: "AsyncSession", *, include_inactive: bool = True) -> list[Plan]:
    stmt = select(Plan).order_by(Plan.is_default.desc(), Plan.name)
    if not include_inactive:  # "only assignable plans": the Internal plan never is
        stmt = stmt.where(Plan.is_active.is_(True), Plan.is_internal.is_(False))
    return list((await session.execute(stmt)).scalars().all())


async def _name_free(
    session: "AsyncSession", name: str, *, except_id: uuid.UUID | None = None
) -> None:
    stmt = select(Plan.id).where(Plan.name == name)
    if except_id is not None:
        stmt = stmt.where(Plan.id != except_id)
    if (await session.execute(stmt)).first() is not None:
        raise _name_taken(name)


async def _unset_other_defaults(session: "AsyncSession", keep: uuid.UUID | None) -> None:
    stmt = update(Plan).where(Plan.is_default.is_(True)).values(is_default=False)
    if keep is not None:
        stmt = stmt.where(Plan.id != keep)
    await session.execute(stmt.execution_options(synchronize_session="fetch"))


async def _flush(session: "AsyncSession", name: str) -> None:
    """Flush; a concurrent writer that took the name first becomes `plan_name_taken`."""
    try:
        await session.flush()
    except IntegrityError as exc:
        if "uq_plans_name" in str(exc.orig):
            raise _name_taken(name) from exc
        raise


async def create_plan(
    session: "AsyncSession", fields: dict[str, Any], *, request: "Request | None"
) -> Plan:
    """A new plan from validated `fields` (name, description, the four caps, is_default)."""
    await _name_free(session, fields["name"])
    await _profile_exists(session, fields.get("pipeline_profile_id"))  # PIP1 profile
    # PIP3 fair use
    fair = await _fair_use_values(
        session,
        {k: fields[k] for k in ("fair_use_limits", "fair_use_policy") if fields.get(k) is not None},
        profile_id=fields.get("pipeline_profile_id"),
    )
    # /PIP3 fair use
    make_default = bool(fields.get("is_default"))
    if make_default:
        await _unset_other_defaults(session, keep=None)
    plan = Plan(
        name=fields["name"],
        description=fields.get("description"),
        **{cap: fields.get(cap) for cap in LIMIT_FIELDS},
        count_cached_answers=bool(fields.get("count_cached_answers", True)),
        is_default=make_default,
        is_active=True,
        # BIZ3 plan
        website_learning_enabled=bool(fields.get("website_learning_enabled", True)),
        website_pages_max=fields.get("website_pages_max"),
        dashboard_features=_flags(fields.get("dashboard_features")),
        # /BIZ3 plan
        pipeline_profile_id=fields.get("pipeline_profile_id"),  # PIP1 profile
        fair_use_limits=fair.get("fair_use_limits", {}),  # PIP3 fair use
        fair_use_policy=fair.get("fair_use_policy", {}),  # PIP3 fair use
        channels=_channels(fields.get("channels")),  # TV6 channel
        byok_allowed=bool(fields.get("byok_allowed", True)),  # P5 plans
        self_service=bool(fields.get("self_service", False)),  # P6 plan choice
    )
    session.add(plan)
    await _flush(session, plan.name)
    await record_audit(
        session,
        request,
        "plan.created",
        target_type="plan",
        target_id=str(plan.id),
        details={
            "name": plan.name,
            "is_default": make_default,
            "count_cached_answers": plan.count_cached_answers,
            "website_learning_enabled": plan.website_learning_enabled,  # BIZ3 plan
            "website_pages_max": plan.website_pages_max,
            "channels": plan.channels,  # TV6 channel
            "byok_allowed": plan.byok_allowed,  # P5 plans
            "self_service": plan.self_service,  # P6 plan choice
            "pipeline_profile_id": (  # PIP1 profile
                str(plan.pipeline_profile_id) if plan.pipeline_profile_id else None
            ),
            **{cap: getattr(plan, cap) for cap in LIMIT_FIELDS},
        },
    )
    return plan


async def update_plan(
    session: "AsyncSession", plan: Plan, changes: dict[str, Any], *, request: "Request | None"
) -> Plan:
    """Apply `changes` (only the fields the caller sent; a cap set to None = unlimited).

    Raises:
        APIError 409 `plan_is_default` (un-defaulting or deactivating the default, or
            making an inactive plan the default), `plan_name_taken`.
    """
    changes = {k: v for k, v in changes.items() if k in EDITABLE_FIELDS}
    if plan.is_internal:
        raise _internal_locked()
    will_be_default = changes.get("is_default", plan.is_default)
    will_be_active = changes.get("is_active", plan.is_active)
    if plan.is_default and changes.get("is_default") is False:
        raise APIError(
            409,
            "This is the default plan: make another plan the default instead",
            "plan_is_default",
        )
    if will_be_default and not will_be_active:
        raise APIError(409, "The default plan cannot be deactivated", "plan_is_default")
    if "name" in changes and changes["name"] != plan.name:
        await _name_free(session, changes["name"], except_id=plan.id)
    if "pipeline_profile_id" in changes:  # PIP1 profile
        await _profile_exists(session, changes["pipeline_profile_id"])
    # PIP3 fair use: validated and stored as the sparse dicts; a changed plan profile may no
    # longer be its own downgrade target, so the policy is re-checked then too
    fair_in: dict[str, Any] = {
        k: changes[k] for k in ("fair_use_limits", "fair_use_policy") if k in changes
    }
    policy_after = changes.get("fair_use_policy", plan.fair_use_policy)
    if "pipeline_profile_id" in changes and "fair_use_policy" not in changes and policy_after:
        fair_in["fair_use_policy"] = policy_after
    if fair_in:
        changes.update(
            await _fair_use_values(
                session,
                fair_in,
                profile_id=changes.get("pipeline_profile_id", plan.pipeline_profile_id),
            )
        )
    # /PIP3 fair use

    # BIZ3 plan: flags compare as the full set, so `{}` and every default are the same plan
    if "dashboard_features" in changes:
        changes["dashboard_features"] = _flags(changes["dashboard_features"])
    if "channels" in changes:  # TV6 channel: same rule
        changes["channels"] = _channels(changes["channels"])
    diff: dict[str, dict[str, Any]] = {}
    for field_name, value in changes.items():
        old = getattr(plan, field_name)
        if field_name == "dashboard_features":
            old = _flags(old)
        if field_name == "channels":  # TV6 channel
            old = _channels(old)
        # /BIZ3 plan
        if old != value:
            if field_name == "channels":  # TV6 channel: the audit names each switch
                for key in sorted(old.keys() | value.keys()):
                    if old.get(key) != value.get(key):
                        diff[f"channels.{key}"] = {"from": old.get(key), "to": value.get(key)}
                continue
            if field_name in ("fair_use_limits", "fair_use_policy"):  # PIP3 fair use
                before, after = dict(old or {}), dict(value or {})
                for key in sorted(before.keys() | after.keys()):
                    if before.get(key) != after.get(key):
                        diff[f"{field_name}.{key}"] = {
                            "from": before.get(key),
                            "to": after.get(key),
                        }
                continue
            if field_name == "pipeline_profile_id":  # PIP1 profile: the audit row is JSON
                diff[field_name] = {
                    "from": str(old) if old else None,
                    "to": str(value) if value else None,
                }
                continue
            diff[field_name] = {"from": old, "to": value}
    if not diff:
        return plan
    if diff.get("is_default", {}).get("to") is True:
        await _unset_other_defaults(session, keep=plan.id)
        await session.flush()
    for field_name in {k.split(".", 1)[0] for k in diff}:  # PIP3 fair use / TV6: whole dicts
        setattr(plan, field_name, changes[field_name])  # not the audit's JSON-safe copy
    plan.updated_at = datetime.now(UTC)
    await _flush(session, plan.name)
    await record_audit(
        session,
        request,
        "plan.updated",
        target_type="plan",
        target_id=str(plan.id),
        details={"name": plan.name, "changes": diff},
    )
    return plan


async def delete_plan(session: "AsyncSession", plan: Plan, *, request: "Request | None") -> None:
    """Raises: APIError 409 `plan_is_default`, `plan_in_use` (extra `tenant_count`),
    `plan_internal_locked`."""
    if plan.is_internal:
        raise _internal_locked()
    if plan.is_default:
        raise APIError(
            409,
            "The default plan cannot be deleted: make another plan the default first",
            "plan_is_default",
        )
    in_use = (await tenant_counts(session, [plan.id])).get(plan.id, 0)
    if in_use:
        raise _in_use(in_use)
    await record_audit(
        session,
        request,
        "plan.deleted",
        target_type="plan",
        target_id=str(plan.id),
        details={"name": plan.name},
    )
    await session.delete(plan)
    try:
        await session.flush()
    except IntegrityError as exc:  # a tenant was put on it meanwhile (FK RESTRICT)
        raise _in_use(None) from exc


def _in_use(count: int | None) -> APIError:
    extra: dict[str, Any] = {"tenant_count": count} if count is not None else {}
    return APIError(
        409,
        "Tenants are on this plan: move them to another plan, or deactivate it instead",
        "plan_in_use",
        **extra,
    )


async def set_tenant_plan(
    session: "AsyncSession", tenant: Tenant, plan_id: uuid.UUID, *, request: "Request | None"
) -> Plan:
    """Put `tenant` (a session-attached row) on an active plan; audited.

    Setting the plan it already has changes nothing and writes no audit row.

    Raises:
        APIError 404 `plan_not_found`, 409 `plan_inactive`, 409 `plan_not_assignable`.
    """
    plan = await assignable_plan(session, plan_id, tenant_is_operator=bool(tenant.is_operator))
    if tenant.plan_id == plan.id:
        return plan
    # BIL2 currency
    from callobuzz_rag.billing.pricing import check_plan_currency

    await check_plan_currency(session, tenant.id, plan.id)
    # /BIL2 currency
    previous = await session.get(Plan, tenant.plan_id) if tenant.plan_id else None
    tenant.plan_id = plan.id
    await record_audit(
        session,
        request,
        "tenant.plan_changed",
        tenant_id=tenant.id,
        tenant_label=tenant.name,
        target_type="tenant",
        target_id=str(tenant.id),
        details={
            "from": _summary(previous) if previous is not None else None,
            "to": _summary(plan),
        },
    )
    return plan
