"""Billing accounts: one per workspace, created on first need.

There is no backfill over every tenant. The admin pages, the owner's `GET /billing/account` and
(later) the charge path call `ensure_account`, which is an `INSERT ... ON CONFLICT DO NOTHING`
followed by a `SELECT`, so two callers racing for the same workspace end with one row.
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.accounts.settings_store import read_engine_settings_row
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing import ledger
from callobuzz_rag.billing.enforcement import stored_status  # BIL4 state
from callobuzz_rag.billing.pricing import (
    EffectivePricing,
    PricingOverrides,
    TenantNotFoundError,
    build_effective,
    current_pricing,
    overrides_from_stored,
)
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.plan import LIMIT_FIELDS, Plan
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant

if TYPE_CHECKING:
    from fastapi import Request


def is_exempt(tenant: Tenant, plan: Plan) -> bool:
    """The operator's own workspace, the COB system workspace and the Internal plan are never
    billed."""
    return bool(tenant.is_operator or tenant.is_system or plan.is_internal)


async def ensure_account(session: AsyncSession, tenant_id: uuid.UUID) -> BillingAccount:
    """The workspace's billing account, created if it does not exist yet.

    The currency is the plan pricing's, else the engine's default currency; a plan with a trial
    gives the new account its trial end. Does not commit.

    Raises:
        TenantNotFoundError: no such workspace.
    """
    existing = await _select(session, tenant_id)
    if existing is not None:
        return existing
    row = (
        await session.execute(
            select(Tenant, PlanPricing)
            .select_from(Tenant)
            .join(Plan, Plan.id == Tenant.plan_id)
            .outerjoin(
                PlanPricing,
                and_(PlanPricing.plan_id == Plan.id, PlanPricing.is_current.is_(True)),
            )
            .where(Tenant.id == tenant_id)
        )
    ).first()
    if row is None:
        raise TenantNotFoundError(f"Workspace {tenant_id} not found.")
    _, pricing = row
    if pricing is not None:
        currency = pricing.currency
        trial_days = pricing.trial_days
    else:
        currency = (await read_engine_settings_row(session))[0].billing.default_currency
        trial_days = 0
    values: dict[str, object] = {"tenant_id": tenant_id, "currency": currency}
    if trial_days:
        values["trial_ends_at"] = datetime.now(UTC) + timedelta(days=trial_days)
    await session.execute(
        insert(BillingAccount).values(**values).on_conflict_do_nothing(index_elements=["tenant_id"])
    )
    account = await _select(session, tenant_id)
    if account is None:  # pragma: no cover - the insert or a concurrent one made it
        raise TenantNotFoundError(f"Workspace {tenant_id} not found.")
    return account


async def account_for_update(session: AsyncSession, tenant_id: uuid.UUID) -> BillingAccount:
    """The account row, locked (`SELECT ... FOR UPDATE`), created first if missing. Every
    writer of the balance, the currency or the overrides goes through this so they serialise."""
    await ensure_account(session, tenant_id)
    account = (
        await session.execute(
            select(BillingAccount)
            .where(BillingAccount.tenant_id == tenant_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if account is None:  # pragma: no cover - ensure_account just made it
        raise TenantNotFoundError(f"Workspace {tenant_id} not found.")
    return account


async def _select(session: AsyncSession, tenant_id: uuid.UUID) -> BillingAccount | None:
    return (
        await session.execute(
            select(BillingAccount)
            .where(BillingAccount.tenant_id == tenant_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------- the admin's view and edits


@dataclass(frozen=True)
class BillingContext:
    """Everything the billing pages show about one workspace, loaded with simple queries."""

    tenant: Tenant
    plan: Plan
    pricing: PlanPricing | None
    account: BillingAccount
    effective: EffectivePricing


async def load_context(session: AsyncSession, tenant_id: uuid.UUID) -> BillingContext:
    """The workspace's account (created if missing) with its plan, pricing and effective terms.

    Raises:
        TenantNotFoundError: no such workspace.
    """
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise TenantNotFoundError(f"Workspace {tenant_id} not found.")
    account = await ensure_account(session, tenant_id)
    plan = await session.get(Plan, tenant.plan_id)
    if plan is None:  # pragma: no cover - tenants.plan_id is a RESTRICT foreign key
        raise TenantNotFoundError(f"Workspace {tenant_id} has no plan.")
    pricing = await current_pricing(session, plan.id)
    return BillingContext(
        tenant, plan, pricing, account, build_effective(tenant, plan, pricing, account)
    )


async def list_rows(
    session: AsyncSession,
    *,
    q: str | None,
    payment_type: str | None,
    offset: int,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    """One row per workspace (the COB system workspace is never listed), by name, filtered by a
    name search and the effective payment type, then paged. Workspaces without an account yet
    appear with the plan's terms (`has_account: false`); opening one creates it."""
    tenants = list(
        (
            await session.execute(
                select(Tenant).where(Tenant.is_system.is_(False)).order_by(Tenant.name)
            )
        )
        .scalars()
        .all()
    )
    if q:
        needle = q.strip().casefold()
        tenants = [t for t in tenants if needle in t.name.casefold()]
    if not tenants:
        return [], 0
    plan_ids = list({t.plan_id for t in tenants})
    plans = {
        p.id: p
        for p in (await session.execute(select(Plan).where(Plan.id.in_(plan_ids)))).scalars().all()
    }
    pricings = {
        p.plan_id: p
        for p in (
            await session.execute(
                select(PlanPricing).where(
                    PlanPricing.plan_id.in_(plan_ids), PlanPricing.is_current.is_(True)
                )
            )
        )
        .scalars()
        .all()
    }
    accounts = {
        a.tenant_id: a
        for a in (
            await session.execute(
                select(BillingAccount).where(BillingAccount.tenant_id.in_([t.id for t in tenants]))
            )
        )
        .scalars()
        .all()
    }
    default_currency = (await read_engine_settings_row(session))[0].billing.default_currency
    rows: list[dict[str, Any]] = []
    for tenant in tenants:
        plan = plans[tenant.plan_id]
        account = accounts.get(tenant.id)
        eff = build_effective(
            tenant, plan, pricings.get(plan.id), account, default_currency=default_currency
        )
        if payment_type and eff.fields.payment_type != payment_type:
            continue
        rows.append(
            {
                "tenant_id": tenant.id,
                "tenant_name": tenant.name,
                "plan": {"id": plan.id, "name": plan.name},
                "currency": eff.currency,
                "exempt": eff.exempt,
                "payment_type": eff.fields.payment_type,
                "balance_minor": account.balance_minor if account is not None else 0,
                "pause_override": account.pause_override if account is not None else "auto",
                "overrides_count": len(eff.overridden),
                "has_account": account is not None,
                "status": stored_status(eff, account),  # BIL4 state
            }
        )
    return rows[offset : offset + limit], len(rows)


def _invalid(loc: tuple[str, ...], message: str, value: Any) -> RequestValidationError:
    return RequestValidationError(
        [{"type": "value_error", "loc": ("body", *loc), "msg": message, "input": value}]
    )


def merge_overrides(stored: Mapping[str, Any], changes: Mapping[str, Any]) -> dict[str, Any]:
    """The stored overrides with `changes` applied: a value sets a field, `None` removes it.

    Raises:
        RequestValidationError: an unknown field, or a value the field does not accept.
    """
    merged = dict(overrides_from_stored(stored).set_fields())
    for key, value in changes.items():
        if key not in PricingOverrides.model_fields:
            raise _invalid(("overrides", key), "Not a pricing field that can be overridden", value)
        if value is None:
            merged.pop(key, None)
            continue
        try:
            PricingOverrides.model_validate({key: value})
        except ValidationError as exc:
            raise _invalid(("overrides", key), exc.errors()[0]["msg"], value) from exc
        merged[key] = value
    return merged


def check_limit_overrides(limits: Mapping[str, Any]) -> dict[str, int | None]:
    """Per-customer limits: only the plan's limit fields, each an integer >= 1 or null
    (unlimited).

    Raises:
        RequestValidationError: an unknown limit, or a value below 1.
    """
    out: dict[str, int | None] = {}
    for key, value in limits.items():
        if key not in LIMIT_FIELDS:
            raise _invalid(("limit_overrides", key), "Not a limit a plan sets", value)
        if value is not None and (type(value) is not int or value < 1):
            raise _invalid(
                ("limit_overrides", key), "A limit is a whole number of at least 1", value
            )
        out[key] = value
    return out


def _exempt_refusal() -> APIError:
    return APIError(
        409,
        "This is your own workspace (or an internal one): it is never billed, so only its "
        "notes can be edited.",
        "billing_exempt",
    )


async def update_account(
    session: AsyncSession,
    ctx: BillingContext,
    changes: Mapping[str, Any],
    *,
    request: "Request | None",
) -> list[str]:
    """Apply an admin's PATCH to the account and audit it (the caller commits).

    `changes` holds only the fields the request sent. Returns the changed field names
    (`overrides.<field>`, `limit_overrides.<field>`, `currency`, `pause_override`, `billing_day`,
    `notes`); nothing is written, and no audit row either, when nothing changes.

    Raises:
        APIError 409 `billing_exempt` (anything but notes on an exempt workspace),
        409 `currency_locked` (the account already has transactions).
        RequestValidationError: an invalid override or limit.
    """
    account = ctx.account
    if ctx.effective.exempt and set(changes) - {"notes"}:
        raise _exempt_refusal()
    changed: list[str] = []
    new_values: dict[str, Any] = {}

    if changes.get("overrides") is not None:
        before = overrides_from_stored(account.overrides).set_fields()
        merged = merge_overrides(account.overrides, changes["overrides"])
        changed += [
            f"overrides.{key}"
            for key in sorted(set(before) | set(merged))
            if before.get(key) != merged.get(key)
        ]
        new_values["overrides"] = merged
    if changes.get("limit_overrides") is not None:
        limits = check_limit_overrides(changes["limit_overrides"])
        stored_limits = dict(account.limit_overrides or {})
        changed += [
            f"limit_overrides.{key}"
            for key in sorted(set(stored_limits) | set(limits))
            if (key in stored_limits) != (key in limits)
            or stored_limits.get(key) != limits.get(key)
        ]
        new_values["limit_overrides"] = limits
    if changes.get("currency") is not None and changes["currency"] != account.currency:
        if await ledger.currency_frozen(session, account):
            raise APIError(
                409,
                "This customer already has transactions or usage not yet posted, so the "
                "currency can no longer change.",
                "currency_locked",
            )
        if ctx.pricing is not None and changes["currency"] != ctx.pricing.currency:
            raise APIError(
                409,
                f"This customer's plan is priced in {ctx.pricing.currency}, so their account "
                "stays in that currency. Change the plan's pricing currency instead.",
                "currency_mismatch",
                account_currency=account.currency,
                currency=ctx.pricing.currency,
            )
        changed.append("currency")
        new_values["currency"] = changes["currency"]
    for field in ("pause_override", "billing_day"):
        if changes.get(field) is not None and getattr(account, field) != changes[field]:
            changed.append(field)
            new_values[field] = changes[field]
    if "notes" in changes and account.notes != changes["notes"]:
        changed.append("notes")
        new_values["notes"] = changes["notes"]
    if not changed:
        return []
    for field, value in new_values.items():
        setattr(account, field, value)
    account.updated_at = datetime.now(UTC)
    await session.flush()
    details: dict[str, Any] = {"changed": changed}
    if "pause_override" in new_values:
        details["pause_override"] = new_values["pause_override"]
    await record_audit(
        session,
        request,
        "billing.account_updated",
        tenant_id=ctx.tenant.id,
        tenant_label=ctx.tenant.name,
        target_type="billing_account",
        target_id=str(account.id),
        details=details,
    )
    return changed
