"""Which pipeline profile a business answers with, and the stored-profile helpers (PIP1).

Resolution order (`plan_profile`): the operator workspace, the COB system workspace and the
Internal plan are exempt and always get the Premium profile; otherwise the business's own
override; otherwise its plan's profile; otherwise the default profile. One indexed query.
"""

import logging
import uuid
from dataclasses import replace
from datetime import UTC, datetime  # P5 plans
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from callobuzz_rag.billing.byok import active_grant_exists, allowance  # P5 plans
from callobuzz_rag.db.models.pipeline_profile import PipelineProfile, TenantPipelineSettings
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.tiers.apply import local_ready
from callobuzz_rag.tiers.schema import EffectiveProfile, ProfileConfig, ProfileSource

logger = logging.getLogger(__name__)


def parse_config(raw: Any, *, name: str = "?") -> ProfileConfig:
    """A stored profile config, re-validated on read.

    A stored key that no longer validates (a setting renamed by a later release, a hand edit)
    is dropped with a warning, so one bad key never stops a business from answering. A config
    that is invalid as a whole (a rule between fields) falls back to the defaults.
    """
    data = dict(raw) if isinstance(raw, dict) else {}
    for _ in range(len(data) + 1):
        try:
            return ProfileConfig.model_validate(data)
        except ValidationError as exc:
            bad = {
                str(err["loc"][0])
                for err in exc.errors()
                if err.get("loc") and err["loc"][0] in data
            }
            if not bad:
                logger.warning("Pipeline profile %s is invalid (%s); using the defaults", name, exc)
                return ProfileConfig()
            logger.warning("Pipeline profile %s: dropping invalid keys %s", name, sorted(bad))
            data = {k: v for k, v in data.items() if k not in bad}
    return ProfileConfig()  # pragma: no cover - the loop always returns


def _available(config: ProfileConfig, settings: Any) -> bool:
    """An engine profile always answers; a local one needs the local model ready AND, when the
    profile names its own model, that model downloaded on the server."""
    if config.answer_route == "engine":
        return True
    if not local_ready(settings):
        return False
    if config.answer_model is None:
        return True
    from callobuzz_rag.llm.local import local_models, same_model

    return any(same_model(m, config.answer_model) for m in local_models(settings))


def effective(
    row: PipelineProfile | None,
    source: ProfileSource,
    *,
    settings: Any = None,
    byok_allowed: bool = True,  # P5 plans
) -> EffectiveProfile:
    """An `EffectiveProfile` from a stored row; None is the built-in Standard (a no-op)."""
    if row is None:
        return EffectiveProfile(
            id=None,
            name="Standard",
            label="Standard",
            config=ProfileConfig(),
            source="builtin_fallback",
            available=True,
            byok_allowed=byok_allowed,  # P5 plans
        )
    config = parse_config(row.config, name=row.name)
    return EffectiveProfile(
        id=row.id,
        name=row.name,
        label=row.label,
        config=config,
        source=source,
        available=_available(config, settings),
        byok_allowed=byok_allowed,  # P5 plans
    )


async def _looked_at(profile: EffectiveProfile, settings: Any) -> EffectiveProfile:
    """A profile that answers on the local model, judged against a readiness cache that is
    current. A process without the API's refresher (a Celery worker, a one-off job) has a cold
    cache, so the local model would look unavailable and a Free business would be paused for
    nothing: look once (no-op while the cache is fresh, never raises, never loads the model).
    Profiles on the engine's own route cause no check at all."""
    if profile.config.answer_route != "local" or settings is None:
        return profile
    from callobuzz_rag.llm.local import ensure_status

    await ensure_status(settings)
    return replace(profile, available=_available(profile.config, settings))


async def plan_profile(
    session: AsyncSession, tenant_id: uuid.UUID, *, settings: Any = None
) -> EffectiveProfile:
    """The profile this business answers with. One query (tenant, its override, its plan's
    profile, the default and Premium, joined)."""
    return await _looked_at(await _plan_profile(session, tenant_id, settings), settings)


async def _plan_profile(
    session: AsyncSession, tenant_id: uuid.UUID, settings: Any
) -> EffectiveProfile:
    override = aliased(PipelineProfile)
    planned = aliased(PipelineProfile)
    default = aliased(PipelineProfile)
    premium = aliased(PipelineProfile)
    stmt = (
        select(
            Tenant.is_operator,
            Tenant.is_system,
            Plan.is_internal,
            Plan.byok_allowed,  # P5 plans: the same statement answers "may it use its own key"
            active_grant_exists(datetime.now(UTC)),  # P5 plans
            override,
            planned,
            default,
            premium,
        )
        .select_from(Tenant)
        .join(Plan, Plan.id == Tenant.plan_id)
        .outerjoin(TenantPipelineSettings, TenantPipelineSettings.tenant_id == Tenant.id)
        .outerjoin(override, override.id == TenantPipelineSettings.profile_id)
        .outerjoin(planned, planned.id == Plan.pipeline_profile_id)
        .outerjoin(default, default.is_default.is_(True))
        .outerjoin(premium, premium.builtin_key == "premium")
        .where(Tenant.id == tenant_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return effective(None, "builtin_fallback", settings=settings)
    is_operator, is_system, is_internal, plan_byok, byok_grant, ov, pl, df, pr = row
    if is_operator or is_system or is_internal:
        return effective(pr or df, "exempt", settings=settings)
    # P5 plans
    byok, _source = allowance(exempt=False, plan_flag=bool(plan_byok), grant=bool(byok_grant))
    # /P5 plans
    if ov is not None:
        return effective(ov, "override", settings=settings, byok_allowed=byok)
    if pl is not None:
        return effective(pl, "plan", settings=settings, byok_allowed=byok)
    return effective(df, "default", settings=settings, byok_allowed=byok)


async def load_profile(
    session: AsyncSession, profile_id: uuid.UUID | None, *, settings: Any = None
) -> EffectiveProfile | None:
    """One stored profile by id; None id is the built-in Free profile (the downgrade target
    that needs no choice); an unknown id is None."""
    if profile_id is None:
        row = (
            await session.execute(
                select(PipelineProfile).where(PipelineProfile.builtin_key == "free")
            )
        ).scalar_one_or_none()
    else:
        row = (
            await session.execute(select(PipelineProfile).where(PipelineProfile.id == profile_id))
        ).scalar_one_or_none()
    if row is None:
        return None
    return await _looked_at(effective(row, "plan", settings=settings), settings)


def noop_profile() -> EffectiveProfile:
    """The profile that changes nothing: for code that answers with its own configuration
    (COB) or has no business to look up."""
    return effective(None, "builtin_fallback")


def downgraded(target: EffectiveProfile) -> EffectiveProfile:
    """The target of a downgrade: its own settings, always at low priority, so a business that
    is being slowed never competes with one that is not. Its settings replace the knowledge
    base's (a downgraded answer is charged nothing, so the business's own expensive model must
    not keep running at the deployer's cost), whatever the stored profile says."""
    return replace(
        target,
        source="downgrade",
        config=target.config.model_copy(update={"priority": "low", "overrides_kb_settings": True}),
    )
