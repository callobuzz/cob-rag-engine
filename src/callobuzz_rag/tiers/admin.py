"""The deployer's profile catalogue: create, edit, clone, delete, default, per-business choice
(PIP1; the routes are `api/routes/pipelines.py`).

Like `accounts/plans.py`, none of these functions commits; each writes its audit row into the
caller's session. The queries are plain selects, so the in-memory database of the unit tests
can run them. Runtime resolution (`plan_profile`) lives in `profiles.py`.
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.pipeline_profile import PipelineProfile, TenantPipelineSettings
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.tiers.profiles import effective
from callobuzz_rag.tiers.schema import EffectiveProfile, ProfileConfig, rule_text

if TYPE_CHECKING:
    from fastapi import Request


def profile_not_found() -> APIError:
    return APIError(404, "That pipeline profile does not exist.", "profile_not_found")


def _name_taken(name: str) -> APIError:
    return APIError(
        409, f"Another pipeline profile is already called {name!r}.", "profile_name_taken"
    )


def validate_config(raw: Any) -> ProfileConfig:
    """A profile config from a request body. Raises 422 `invalid_profile` whose `detail` names
    the rule that was broken, in words a deployer understands."""
    try:
        return ProfileConfig.model_validate(raw or {})
    except ValidationError as exc:
        raise APIError(422, rule_text(exc), "invalid_profile") from exc


def stored_config(config: ProfileConfig) -> dict[str, Any]:
    """The config as stored: only what differs from the defaults, so a profile that follows
    everything stays `{}` and a setting added later reads its default."""
    return config.model_dump(mode="json", exclude_defaults=True)


async def list_profiles(session: AsyncSession) -> list[PipelineProfile]:
    stmt = select(PipelineProfile).order_by(PipelineProfile.is_default.desc(), PipelineProfile.name)
    return list((await session.execute(stmt)).scalars().all())


async def get_profile(session: AsyncSession, profile_id: uuid.UUID) -> PipelineProfile:
    """Raises: APIError 404 `profile_not_found`."""
    row = (
        await session.execute(select(PipelineProfile).where(PipelineProfile.id == profile_id))
    ).scalar_one_or_none()
    if row is None:
        raise profile_not_found()
    return row


async def default_profile(session: AsyncSession) -> PipelineProfile | None:
    return (
        await session.execute(select(PipelineProfile).where(PipelineProfile.is_default.is_(True)))
    ).scalar_one_or_none()


async def usage_counts(
    session: AsyncSession, ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    """(plans by profile, businesses by profile): who picks each profile explicitly. A plan with
    no profile uses the default one, so the default's plan count also takes those in."""
    plan_counts: dict[uuid.UUID, int] = {}
    tenant_counts: dict[uuid.UUID, int] = {}
    if not ids:
        return plan_counts, tenant_counts
    plan_rows = await session.execute(
        select(Plan.pipeline_profile_id).where(Plan.pipeline_profile_id.in_(ids))
    )
    for (pid,) in plan_rows.all():
        if pid is not None:
            plan_counts[pid] = plan_counts.get(pid, 0) + 1
    tenant_rows = await session.execute(
        select(TenantPipelineSettings.profile_id).where(TenantPipelineSettings.profile_id.in_(ids))
    )
    for (pid,) in tenant_rows.all():
        if pid is not None:
            tenant_counts[pid] = tenant_counts.get(pid, 0) + 1
    default = await default_profile(session)
    if default is not None and default.id in ids:
        unset = (
            await session.execute(select(Plan.id).where(Plan.pipeline_profile_id.is_(None)))
        ).all()
        plan_counts[default.id] = plan_counts.get(default.id, 0) + len(unset)
    return plan_counts, tenant_counts


async def profile_labels(session: AsyncSession, plans: list[Plan]) -> dict[uuid.UUID, str]:
    """plan id -> the label of the profile it answers with (the default's for a plan that picks
    none), for the plan screens."""
    wanted = {p.pipeline_profile_id for p in plans if p.pipeline_profile_id is not None}
    rows = list((await session.execute(select(PipelineProfile))).scalars().all())
    by_id = {r.id: r.label for r in rows if r.id in wanted}
    default = next((r for r in rows if r.is_default), None)
    out: dict[uuid.UUID, str] = {}
    for plan in plans:
        label = (
            by_id.get(plan.pipeline_profile_id)
            if plan.pipeline_profile_id is not None
            else (default.label if default is not None else None)
        )
        if label is not None:
            out[plan.id] = label
    return out


def resolved_view(config: ProfileConfig, settings: Any) -> dict[str, Any]:
    """What the profile's null fields mean now: the engine's own values."""
    local = config.answer_route == "local"
    if config.reranker == "inherit":
        engine_reranks = bool(getattr(settings, "reranking_enabled", False))
        reranker = str(getattr(settings, "rerank_provider", "local")) if engine_reranks else "off"
    else:
        reranker = config.reranker
    engine_max = int(settings.llm_max_tokens)
    if local:
        answer_model = config.answer_model or str(
            getattr(settings, "local_llm_model", "") or "the local model"
        )
        max_tokens = config.max_answer_tokens or engine_max
    else:
        answer_model = config.answer_model or str(settings.llm_model)
        max_tokens = min(config.max_answer_tokens or engine_max, engine_max)
    language_models = (
        config.language_models
        if config.language_models is not None
        else (getattr(settings, "llm_language_models", None) or {})
    )
    return {
        "answer_model": answer_model,
        "thinking": config.thinking or (str(getattr(settings, "llm_thinking", "") or "") or None),
        "fast_model": config.fast_model or (str(settings.llm_fast_model or "") or None),
        "language_models": dict(language_models),
        "reranker": reranker,
        "rerank_model": config.rerank_model
        or (str(getattr(settings, "rerank_llm_model", "") or "") or None),
        "top_k": config.top_k or int(settings.query_top_k),
        "final_k": config.final_k or int(settings.query_final_k),
        "multi_query_count": (
            config.multi_query_count
            if config.multi_query_count is not None
            else int(settings.multi_query_count)
        ),
        "hyde_enabled": (
            config.hyde_enabled if config.hyde_enabled is not None else bool(settings.hyde_enabled)
        ),
        "query_classification_enabled": (
            config.query_classification_enabled
            if config.query_classification_enabled is not None
            else bool(settings.query_classification_enabled)
        ),
        "max_answer_tokens": max_tokens,
        "followups": config.followups,
        "priority": config.priority,
        "max_concurrency": config.max_concurrency,
    }


async def _name_free(
    session: AsyncSession, name: str, *, except_id: uuid.UUID | None = None
) -> None:
    stmt = select(PipelineProfile.id).where(PipelineProfile.name == name)
    if except_id is not None:
        stmt = stmt.where(PipelineProfile.id != except_id)
    if (await session.execute(stmt)).first() is not None:
        raise _name_taken(name)


async def _flush(session: AsyncSession, name: str) -> None:
    """Flush; a concurrent writer that took the name first becomes `profile_name_taken`."""
    try:
        await session.flush()
    except IntegrityError as exc:
        if "uq_pipeline_profiles_name" in str(exc.orig):
            raise _name_taken(name) from exc
        raise


async def create_profile(
    session: AsyncSession, fields: dict[str, Any], *, request: "Request | None"
) -> PipelineProfile:
    """A new profile from request `fields` (name, label, description, config)."""
    config = validate_config(fields.get("config"))
    name = fields["name"].strip()
    await _name_free(session, name)
    row = PipelineProfile(
        name=name,
        label=fields["label"].strip(),
        description=fields.get("description"),
        builtin_key=None,
        is_default=False,
        config=stored_config(config),
        updated_by=actor_from_request(request)[2],
    )
    session.add(row)
    await _flush(session, name)
    await record_audit(
        session,
        request,
        "pipeline.profile_created",
        target_type="pipeline_profile",
        target_id=str(row.id),
        details={"profile_id": str(row.id), "name": row.name},
    )
    return row


async def update_profile(
    session: AsyncSession,
    row: PipelineProfile,
    fields: dict[str, Any],
    *,
    request: "Request | None",
) -> PipelineProfile:
    """Apply the fields the caller sent (`config` replaces the whole config)."""
    changed: list[str] = []
    if fields.get("name") is not None:
        name = fields["name"].strip()
        if name != row.name:
            await _name_free(session, name, except_id=row.id)
            row.name = name
            changed.append("name")
    if fields.get("label") is not None and fields["label"].strip() != row.label:
        row.label = fields["label"].strip()
        changed.append("label")
    if "description" in fields and fields["description"] != row.description:
        row.description = fields["description"]
        changed.append("description")
    if fields.get("config") is not None:
        stored = stored_config(validate_config(fields["config"]))
        if stored != (row.config or {}):
            row.config = stored
            changed.append("config")
    if not changed:
        return row
    row.updated_at = datetime.now(UTC)
    row.updated_by = actor_from_request(request)[2]
    await _flush(session, row.name)
    await record_audit(
        session,
        request,
        "pipeline.profile_updated",
        target_type="pipeline_profile",
        target_id=str(row.id),
        details={"profile_id": str(row.id), "name": row.name, "changed": changed},
    )
    return row


def _in_use(plans: int | None, tenants: int | None, targets: int = 0) -> APIError:
    extra: dict[str, Any] = {}
    if plans is not None and tenants is not None:
        # `downgrade_targets`: fair-use policies (of plans and of single businesses) that name
        # this profile as the one to fall back to (PIP3)
        extra = {"plans": plans, "tenants": tenants, "downgrade_targets": targets}
    return APIError(
        409,
        "Plans or businesses use this profile: move them to another profile first.",
        "profile_in_use",
        **extra,
    )


async def delete_profile(
    session: AsyncSession, row: PipelineProfile, *, request: "Request | None"
) -> None:
    """Raises: APIError 409 `profile_is_default`, `profile_in_use` (extras `plans`, `tenants`,
    `downgrade_targets`)."""
    if row.is_default:
        raise APIError(
            409,
            "This is the default profile: make another profile the default first.",
            "profile_is_default",
        )
    plan_counts, tenant_counts = await usage_counts(session, [row.id])
    from callobuzz_rag.fairuse.admin import downgrade_target_count  # PIP3 fair use

    targets = await downgrade_target_count(session, row.id)
    if plan_counts.get(row.id) or tenant_counts.get(row.id) or targets:
        raise _in_use(plan_counts.get(row.id, 0), tenant_counts.get(row.id, 0), targets)
    await record_audit(
        session,
        request,
        "pipeline.profile_deleted",
        target_type="pipeline_profile",
        target_id=str(row.id),
        details={"profile_id": str(row.id), "name": row.name},
    )
    await session.delete(row)
    try:
        await session.flush()
    except IntegrityError as exc:  # a plan or business was given it meanwhile (FK RESTRICT)
        raise _in_use(None, None) from exc


async def clone_profile(
    session: AsyncSession,
    source: PipelineProfile,
    *,
    name: str,
    label: str | None,
    request: "Request | None",
) -> PipelineProfile:
    """A copy of `source` under a new name (never the default, never a built-in)."""
    return await create_profile(
        session,
        {
            "name": name,
            "label": label or source.label,
            "description": source.description,
            "config": dict(source.config or {}),
        },
        request=request,
    )


async def make_default(
    session: AsyncSession, row: PipelineProfile, *, request: "Request | None"
) -> PipelineProfile:
    """The one default profile, swapped in a single transaction (the old one is cleared before
    the new one is set, so the unique index is never broken in between)."""
    previous = await default_profile(session)
    if previous is not None and previous.id == row.id:
        return row
    await session.execute(
        update(PipelineProfile)
        .where(PipelineProfile.is_default.is_(True))
        .values(is_default=False)
        .execution_options(synchronize_session="fetch")
    )
    await session.execute(
        update(PipelineProfile)
        .where(PipelineProfile.id == row.id)
        .values(is_default=True, updated_at=datetime.now(UTC))
        .execution_options(synchronize_session="fetch")
    )
    row.is_default = True
    await record_audit(
        session,
        request,
        "pipeline.default_set",
        target_type="pipeline_profile",
        target_id=str(row.id),
        details={
            "profile_id": str(row.id),
            "previous_id": str(previous.id) if previous is not None else None,
        },
    )
    return row


async def _override_row(
    session: AsyncSession, tenant_id: uuid.UUID
) -> TenantPipelineSettings | None:
    return (
        await session.execute(
            select(TenantPipelineSettings).where(TenantPipelineSettings.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()


async def tenant_choice(
    session: AsyncSession, tenant: Tenant
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """(the profile the business's plan picks, the profile given to it directly)."""
    plan = (
        await session.execute(select(Plan).where(Plan.id == tenant.plan_id))
    ).scalar_one_or_none()
    override = await _override_row(session, tenant.id)
    return (
        plan.pipeline_profile_id if plan is not None else None,
        override.profile_id if override is not None else None,
    )


async def effective_for_tenant(
    session: AsyncSession, tenant: Tenant, *, settings: Any = None
) -> EffectiveProfile:
    """The profile a business answers with, by the same order as `plan_profile`, from simple
    selects (the admin screens use it)."""
    plan = (
        await session.execute(select(Plan).where(Plan.id == tenant.plan_id))
    ).scalar_one_or_none()
    plan_id, override_id = await tenant_choice(session, tenant)
    default = await default_profile(session)
    if tenant.is_operator or tenant.is_system or (plan is not None and plan.is_internal):
        premium = (
            await session.execute(
                select(PipelineProfile).where(PipelineProfile.builtin_key == "premium")
            )
        ).scalar_one_or_none()
        return effective(premium or default, "exempt", settings=settings)
    for source, pid in (("override", override_id), ("plan", plan_id)):
        if pid is None:
            continue
        row = (
            await session.execute(select(PipelineProfile).where(PipelineProfile.id == pid))
        ).scalar_one_or_none()
        if row is not None:
            return effective(row, source, settings=settings)  # type: ignore[arg-type]
    return effective(default, "default", settings=settings)


async def set_tenant_profile(
    session: AsyncSession,
    tenant: Tenant,
    profile_id: uuid.UUID | None,
    *,
    request: "Request | None",
) -> None:
    """Give a business a profile of its own (None: back to its plan's). Audited; setting what
    it already has changes nothing and writes no audit row."""
    if profile_id is not None:
        await get_profile(session, profile_id)
    row = await _override_row(session, tenant.id)
    previous = row.profile_id if row is not None else None
    if previous == profile_id:
        return
    actor = actor_from_request(request)[2]
    if profile_id is None:
        # Never delete the row: it also holds the business's fair-use overrides and state.
        if row is not None:
            row.profile_id = None
            row.updated_at = datetime.now(UTC)
            row.updated_by = actor
    elif row is None:
        session.add(
            TenantPipelineSettings(tenant_id=tenant.id, profile_id=profile_id, updated_by=actor)
        )
    else:
        row.profile_id = profile_id
        row.updated_at = datetime.now(UTC)
        row.updated_by = actor
    await session.flush()
    await record_audit(
        session,
        request,
        "pipeline.tenant_profile_set",
        tenant_id=tenant.id,
        tenant_label=tenant.name,
        target_type="tenant",
        target_id=str(tenant.id),
        details={
            "profile_id": str(profile_id) if profile_id is not None else None,
            "previous_id": str(previous) if previous is not None else None,
        },
    )
