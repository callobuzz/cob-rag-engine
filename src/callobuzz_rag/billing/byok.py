"""Whether a workspace may use its own AI key (P5). The only place that decides.

A business can answer with a provider key of its own only when its plan says so
(`plans.byok_allowed`) or it holds an active `feature_unlock` add-on for `byok`. The operator
workspace, the COB system workspace and the Internal plan are always allowed. Without the
allowance the stored key is *kept* but not used: answers fall back to the deployer's model on
the deployer's key (and the business's credits), and saving a new key is refused with 403
`byok_not_in_plan`.

The answer path never calls `byok_allowed` itself: `tiers.profiles` reads the same flag in the
one query that already finds the business's pipeline profile and carries it on
`EffectiveProfile.byok_allowed`; `tiers.resolve.resolve_with_profile` applies
`usable_llm_config` / `usable_headers` before it resolves anything, so `llm.payer` is
`operator` and every gate, meter and charge downstream follows unchanged.
`tests/unit/test_byok_everywhere.py` lists every file that may read a stored key.
"""

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.add_on import AddOnGrant
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant

if TYPE_CHECKING:
    from fastapi import Request

FEATURE = "byok"
RENEWAL_GRACE = timedelta(hours=2)

# What a key that is not used takes with it: the connection (key, address, provider) and every
# model choice made for it. A model picked for the business's own key must not run on the
# deployer's (the same rule as a fair-use downgrade).
_KEY_FIELDS = frozenset({"api_key_encrypted", "base_url", "provider"})
_KEY_HEADERS = frozenset({"x-llm-api-key", "x-llm-base-url"})


def model_choices() -> frozenset[str]:
    from callobuzz_rag.tiers.resolve import _MODEL_CHOICES

    return _MODEL_CHOICES


def active_grant_exists(now: datetime) -> ColumnElement[bool]:
    """EXISTS over the workspace's (`tenants.id`) active BYOK grants at `now`: not revoked,
    begun, not ended. A cancelled renewal still counts until its `ends_at`."""
    return (
        exists()
        .where(
            AddOnGrant.tenant_id == Tenant.id,
            AddOnGrant.kind == "feature_unlock",
            AddOnGrant.feature == FEATURE,
            AddOnGrant.revoked_at.is_(None),
            AddOnGrant.starts_at <= now,
            or_(
                AddOnGrant.ends_at.is_(None),
                AddOnGrant.ends_at > now,
                # a renewing grant stays in force until the hourly beat has had its turn: a
                # late renewal must never flip a business to the deployer's model (D17)
                and_(
                    AddOnGrant.renews.is_(True),
                    AddOnGrant.cancelled_at.is_(None),
                    AddOnGrant.ends_at > now - RENEWAL_GRACE,
                ),
            ),
        )
        .correlate(Tenant)
    )


def allowance(
    *, exempt: bool, plan_flag: bool | None, grant: bool | None
) -> tuple[bool, str | None]:
    """`(allowed, source)` from the three facts; `source` is `plan` | `add_on` | None."""
    if exempt or plan_flag:
        return True, "plan"
    if grant:
        return True, "add_on"
    return False, None


async def byok_status(
    session: AsyncSession, tenant_id: uuid.UUID, *, now: datetime | None = None
) -> tuple[bool, str | None]:
    """`(allowed, source)` for a workspace, in one query. An unknown workspace is allowed (there
    is nothing to protect)."""
    moment = now or datetime.now(UTC)
    row = (
        await session.execute(
            select(
                Tenant.is_operator,
                Tenant.is_system,
                Plan.is_internal,
                Plan.byok_allowed,
                active_grant_exists(moment),
            )
            .select_from(Tenant)
            .join(Plan, Plan.id == Tenant.plan_id)
            .where(Tenant.id == tenant_id)
        )
    ).first()
    if row is None:
        return True, "plan"
    is_operator, is_system, is_internal, flag, grant = row
    return allowance(
        exempt=bool(is_operator or is_system or is_internal),
        plan_flag=bool(flag),
        grant=bool(grant),
    )


async def byok_allowed(
    session: AsyncSession, tenant_id: uuid.UUID, *, now: datetime | None = None
) -> bool:
    """Whether this workspace may use its own AI key right now (one query)."""
    return (await byok_status(session, tenant_id, now=now))[0]


async def own_key_status(
    session: AsyncSession, tenant_id: uuid.UUID, *, now: datetime | None = None
) -> tuple[bool, bool, str | None]:
    """`(allowed, key_saved, source)` for the owner's billing view: may the workspace use its own
    AI key, is one saved on any of its knowledge bases (used or not), and what gives the right
    (`plan` | `add_on`)."""
    allowed, source = await byok_status(session, tenant_id, now=now)
    saved = (
        await session.execute(
            select(func.count())
            .select_from(Corpus)
            .where(
                Corpus.tenant_id == tenant_id,
                Corpus.is_system.is_(False),
                func.coalesce(Corpus.llm_config["api_key_encrypted"].as_string(), "") != "",
            )
        )
    ).scalar_one()
    return allowed, bool(saved), source


def usable_llm_config(config: Mapping[str, Any] | None, *, allowed: bool) -> dict[str, Any] | None:
    """The knowledge base's `llm_config` as it may be used: unchanged when `allowed`; else without
    its key, address, provider and model choices (the deployer's model on the deployer's key).
    Model choices go only when an own connection (a key or an address) is stored: without one
    they were picked for the deployer's key and stay. A copy either way: the stored row is never
    changed."""
    if not isinstance(config, Mapping):  # None, or nothing a resolver could read
        return None
    if allowed:
        return dict(config)
    if not has_stored_key(config) and not config.get("base_url"):
        return dict(config)
    drop = _KEY_FIELDS | model_choices()
    return {k: v for k, v in config.items() if k not in drop}


def usable_headers(headers: Mapping[str, str] | None, *, allowed: bool) -> dict[str, str]:
    """The request's `X-LLM-*` headers as they may be used: a key or address sent per request
    is ignored (not refused) without the allowance, so a site built on the API keeps answering."""
    if not headers:
        return {}
    if allowed:
        return dict(headers)
    return {k: v for k, v in headers.items() if k.lower() not in _KEY_HEADERS}


def has_stored_key(config: Mapping[str, Any] | None) -> bool:
    """Whether a knowledge base's `llm_config` carries an own key (used or not)."""
    return bool(isinstance(config, Mapping) and config.get("api_key_encrypted"))


async def plans_with_byok(session: AsyncSession) -> list[str]:
    """Names of the plans a business can pick that include the own-key flag."""
    rows = await session.execute(
        select(Plan.name)
        .where(Plan.byok_allowed.is_(True), Plan.is_active.is_(True), Plan.is_internal.is_(False))
        .order_by(Plan.name)
    )
    return [str(n) for n in rows.scalars().all()]


def _join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def not_in_plan_message(support: str | None, plans: list[str] | None = None) -> str:
    ask = support or "your provider"
    if plans:
        return (
            "Using your own AI key isn't part of your plan. "
            f"It's included in {_join_names(plans)}, or ask {ask} about the add-on."
        )
    return f"Using your own AI key isn't part of your plan. Ask {ask} about the add-on."


async def refuse_key_save(session: AsyncSession, request: "Request", tenant_id: uuid.UUID) -> None:
    """Raise 403 `byok_not_in_plan` unless the workspace may use its own AI key. Call it before
    a route encrypts and stores a new `llm_config.api_key`; removing a key never needs it."""
    if await byok_allowed(session, tenant_id):
        return
    from callobuzz_rag.api.features import support_contact

    message = not_in_plan_message(await support_contact(request), await plans_with_byok(session))
    raise APIError(403, message, "byok_not_in_plan")
