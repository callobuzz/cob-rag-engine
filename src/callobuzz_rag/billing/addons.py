"""Add-ons (BIL5): the catalogue the super admin sells from, and applying one to a customer.

Applying an add-on is two things at once, in one transaction: the money (a credit pack puts its
credit and bonus into the ledger; an extra limit or question pack paid from the balance takes its
price out) and the grant (a row that raises a limit; see `limits.py`). Paid outside the engine
(`external`) or given (`free`), a limit or question pack writes no money entry: the grant and its
audit row are the record. Money moves only through `ledger.append_entries`.

Grants copy the add-on's fields when they are made, so editing or deactivating the catalogue never
rewrites what a customer was given. The grant id is derived from the workspace and the request's
idempotency key, so a double click is one grant and one ledger entry.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass  # P1 payments
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastapi.exceptions import RequestValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import Actor, actor_from_request, record_audit
from callobuzz_rag.accounts.caps import next_month_start
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.billing_addons import (
    ApplyAddOnRequest,
    CreateAddOnRequest,
    check_fields_match_kind,
)
from callobuzz_rag.billing import accounts, ledger, service_orders  # P5 plans
from callobuzz_rag.billing.byok import byok_status  # P5 plans
from callobuzz_rag.billing.charging import NAMESPACE_BILLING
from callobuzz_rag.billing.limits import LIMIT_GRANT_KINDS, is_in_force
from callobuzz_rag.billing.money import Money, pct_of
from callobuzz_rag.billing.subscriptions import add_period
from callobuzz_rag.db.models.add_on import AddOn, AddOnGrant

if TYPE_CHECKING:
    from fastapi import Request

_LIMIT_LABELS = {
    "max_knowledge_bases": "knowledge bases",
    "max_documents": "documents",
    "max_widget_keys": "widget keys",
    "max_upload_mb": "MB per upload",
    "max_document_tokens": "tokens per document",
}
_EDITABLE = (
    "name",
    "description",
    "currency",
    "price_minor",
    "credit_minor",
    "bonus_pct",
    "expiry_days",
    "limit_field",
    "limit_delta",
    "questions",
    "duration",
    "is_active",
    # P5 plans
    "feature",
    "renews",
    "includes_support_months",
    "sac_code",
    "sold_to_owners",
    # /P5 plans
)
_SNAPSHOT = (
    "kind",
    "name",
    "currency",
    "price_minor",
    "credit_minor",
    "bonus_pct",
    "limit_field",
    "limit_delta",
    "questions",
    # P5 plans
    "feature",
    "renews",
    "includes_support_months",
    "sac_code",
    # /P5 plans
)
# P5 plans: grants that show as "in force" to a workspace owner (limits and feature unlocks; a
# credit pack is money, a service is an order)
ACTIVE_GRANT_KINDS = (*LIMIT_GRANT_KINDS, "feature_unlock")
# /P5 plans


_FEATURE_LABELS = {"byok": "Your own AI key"}  # P5 plans


def _day(moment: datetime) -> str:
    return f"{moment.day} {moment:%b}"


def summary(
    *,
    kind: str,
    credit_minor: int | None,
    bonus_pct: int,
    currency: str,
    limit_field: str | None,
    limit_delta: int | None,
    questions: int | None,
    ends_at: datetime | None,
    feature: str | None = None,  # P5 plans
    renews: bool = False,  # P5 plans
    includes_support_months: int | None = None,  # P5 plans
) -> str:
    """One line of what an add-on or grant gives: `+500 questions until 1 Nov`, `+1 widget keys`,
    `₹1,000.00 credit (+10 % bonus)`, `Your own AI key until 1 Nov (renews)`."""
    until = f" until {_day(ends_at)}" if ends_at is not None else ""
    # P5 plans
    if kind == "feature_unlock":
        label = _FEATURE_LABELS.get(feature or "", feature or "A feature")
        return f"{label}{until}{' (renews each period)' if renews else ''}"
    if kind == "service":
        support = (
            f", includes {includes_support_months} month{'s' if includes_support_months != 1 else ''}"
            " of support"
            if includes_support_months
            else ""
        )
        return f"A service{support}{' (renews each period)' if renews else ''}"
    # /P5 plans
    if kind == "credit_pack" and credit_minor is not None:
        bonus = f" (+{bonus_pct} % bonus)" if bonus_pct else ""
        return f"{Money(credit_minor, currency).format()} credit{bonus}"  # type: ignore[arg-type]
    if kind == "question_pack":
        return f"+{questions} questions{until}"
    label = _LIMIT_LABELS.get(limit_field or "", limit_field or "")
    return f"+{limit_delta} {label}{until}"


def grant_summary(grant: AddOnGrant) -> str:
    return summary(
        kind=grant.kind,
        credit_minor=grant.credit_minor,
        bonus_pct=grant.bonus_pct,
        currency=grant.currency,
        limit_field=grant.limit_field,
        limit_delta=grant.limit_delta,
        questions=grant.questions,
        ends_at=grant.ends_at,
        feature=grant.feature,  # P5 plans
        renews=grant.renews,  # P5 plans
        includes_support_months=grant.includes_support_months,  # P5 plans
    )


def grant_is_active(grant: AddOnGrant, now: datetime) -> bool:
    """Whether the grant is in force right now: it raises a limit or unlocks a feature (a credit
    pack and a service never are)."""
    return (
        grant.kind in ACTIVE_GRANT_KINDS  # P5 plans
        and grant.revoked_at is None
        and grant.starts_at <= now
        and (grant.ends_at is None or grant.ends_at > now)
    )


# ------------------------------------------------------------------------- the catalogue


def _name_taken(name: str) -> APIError:
    return APIError(409, f"An add-on named {name!r} already exists", "add_on_name_taken")


def _not_found() -> APIError:
    return APIError(404, "That add-on does not exist.", "add_on_not_found")


def _invalid(message: str) -> RequestValidationError:
    return RequestValidationError(
        [{"type": "value_error", "loc": ("body",), "msg": message, "input": None}]
    )


async def list_add_ons(session: AsyncSession, *, include_inactive: bool) -> list[AddOn]:
    stmt = select(AddOn).order_by(AddOn.kind, AddOn.price_minor, AddOn.name)
    if not include_inactive:
        stmt = stmt.where(AddOn.is_active.is_(True))
    return list((await session.execute(stmt)).scalars().all())


async def get_add_on(session: AsyncSession, add_on_id: uuid.UUID) -> AddOn:
    """Raises 404 `add_on_not_found`."""
    row = await session.get(AddOn, add_on_id)
    if row is None:
        raise _not_found()
    return row


async def _name_free(
    session: AsyncSession, name: str, *, except_id: uuid.UUID | None = None
) -> None:
    stmt = select(AddOn.id).where(AddOn.name == name)
    if except_id is not None:
        stmt = stmt.where(AddOn.id != except_id)
    if (await session.execute(stmt)).first() is not None:
        raise _name_taken(name)


async def _flush(session: AsyncSession, name: str) -> None:
    """Flush; a concurrent writer that took the name first becomes `add_on_name_taken`."""
    try:
        await session.flush()
    except IntegrityError as exc:
        if "uq_add_on_catalogue_name" in str(exc.orig):
            raise _name_taken(name) from exc
        raise


async def create_add_on(
    session: AsyncSession, body: CreateAddOnRequest, *, request: "Request | None"
) -> AddOn:
    """Add `body` to the catalogue and audit it (the caller commits)."""
    name = body.name.strip()
    await _name_free(session, name)
    row = AddOn(
        kind=body.kind,
        name=name,
        description=body.description,
        currency=body.currency,
        price_minor=body.price_minor,
        credit_minor=body.credit_minor,
        bonus_pct=body.bonus_pct,
        expiry_days=body.expiry_days,
        limit_field=body.limit_field,
        limit_delta=body.limit_delta,
        questions=body.questions,
        duration=body.duration,
        # P5 plans
        feature=body.feature,
        renews=body.renews,
        includes_support_months=body.includes_support_months,
        sac_code=body.sac_code,
        sold_to_owners=body.sold_to_owners,
        # /P5 plans
        is_active=True,
    )
    session.add(row)
    await _flush(session, name)
    await record_audit(
        session,
        request,
        "billing.add_on_created",
        target_type="add_on",
        target_id=str(row.id),
        details={"add_on_id": str(row.id), "kind": row.kind, "name": name},
    )
    return row


async def update_add_on(
    session: AsyncSession,
    row: AddOn,
    changes: Mapping[str, Any],
    *,
    request: "Request | None",
) -> list[str]:
    """Apply the fields the request sent. The result must still fit the add-on's kind; existing
    grants keep their copy. Returns the changed field names (nothing written, nothing audited
    when none changed). The caller commits.

    Raises:
        RequestValidationError: the result does not fit the kind.
        APIError 409 `add_on_name_taken`.
    """
    changed = [f for f in _EDITABLE if f in changes and getattr(row, f) != changes[f]]
    # P5 plans: saving the price (even unchanged) or "keep this price" makes a seeded price real
    keep_price = bool(row.placeholder) and (
        "price_minor" in changes or changes.get("placeholder") is False
    )
    if not changed and not keep_price:
        return []
    values = {f: getattr(row, f) for f in _EDITABLE} | {f: changes[f] for f in changed}
    if values["name"] is None or values["currency"] is None or values["price_minor"] is None:
        raise _invalid("name, currency and price cannot be empty.")
    if values["is_active"] is None or values["bonus_pct"] is None:
        raise _invalid("is_active and bonus_pct cannot be empty.")
    if values["renews"] is None or values["sold_to_owners"] is None:  # P5 plans
        raise _invalid("renews and sold_to_owners cannot be empty.")
    try:
        check_fields_match_kind(row.kind, values)
    except ValueError as exc:
        raise _invalid(str(exc)) from exc
    if "name" in changed:
        values["name"] = str(values["name"]).strip()
        await _name_free(session, values["name"], except_id=row.id)
    for field in changed:
        setattr(row, field, values[field])
    if keep_price:
        row.placeholder = False
        if "price_minor" not in changed:
            changed.append("placeholder")
    row.updated_at = datetime.now(UTC)
    await _flush(session, str(values["name"]))
    await record_audit(
        session,
        request,
        "billing.add_on_updated",
        target_type="add_on",
        target_id=str(row.id),
        details={"add_on_id": str(row.id), "kind": row.kind, "changed": changed},
    )
    return changed


async def delete_add_on(session: AsyncSession, row: AddOn, *, request: "Request | None") -> None:
    """Delete an add-on that was never granted (the caller commits).

    Raises:
        APIError 409 `add_on_in_use`: deactivate it instead, its grants keep their history.
    """
    used = (
        await session.execute(select(AddOnGrant.id).where(AddOnGrant.add_on_id == row.id).limit(1))
    ).first()
    if used is not None:
        raise APIError(
            409,
            "This add-on has been given to a customer, so it cannot be deleted. "
            "Deactivate it instead.",
            "add_on_in_use",
        )
    await record_audit(
        session,
        request,
        "billing.add_on_deleted",
        target_type="add_on",
        target_id=str(row.id),
        details={"add_on_id": str(row.id), "kind": row.kind, "name": row.name},
    )
    await session.delete(row)
    try:
        await session.flush()
    except IntegrityError as exc:  # a grant was made meanwhile (RESTRICT)
        raise APIError(
            409,
            "This add-on has been given to a customer, so it cannot be deleted. "
            "Deactivate it instead.",
            "add_on_in_use",
        ) from exc


# ---------------------------------------------------------------------------- the grants


def grant_id_for(tenant_id: uuid.UUID, key: str) -> uuid.UUID:
    """The grant's id: the same workspace and key always give the same one."""
    return uuid.uuid5(NAMESPACE_BILLING, f"{tenant_id}:{key}")


async def list_grants(
    session: AsyncSession, tenant_id: uuid.UUID, *, active_only: bool, now: datetime | None = None
) -> list[AddOnGrant]:
    """The workspace's grants, newest first; `active_only` keeps those raising a limit now."""
    moment = now or datetime.now(UTC)
    stmt = select(AddOnGrant).where(AddOnGrant.tenant_id == tenant_id)
    if active_only:
        stmt = stmt.where(AddOnGrant.kind.in_(ACTIVE_GRANT_KINDS), is_in_force(moment))  # P5 plans
    stmt = stmt.order_by(AddOnGrant.created_at.desc())
    return list((await session.execute(stmt)).scalars().all())


async def active_grants(
    session: AsyncSession, tenant_id: uuid.UUID, *, now: datetime | None = None
) -> Sequence[AddOnGrant]:
    return await list_grants(session, tenant_id, active_only=True, now=now)


# P5 plans
def past_the_pause_line(fields: Any, available: int, price: int) -> bool:
    """Whether taking `price` from a balance with `available` would reach the pause line.

    Mirrors the pause rule (`enforcement.decide`): landing on its line already pauses the
    workspace, so a purchase must stay strictly above it. A prepaid purchase never dips into the
    grace either. One rule for the shop, the admin's wallet purchase and a renewal.
    """
    left = available - price
    if fields.payment_type == "prepaid":
        return bool(left < 0 or left <= -fields.grace_minor)
    return bool(left <= -fields.credit_limit_minor)


def period_end(moment: datetime) -> datetime:
    """When a monthly unlock or renewing service bought at `moment` ends: one full month on,
    whatever the account's own billing period. The add-on is priced per month, so it is never
    tied to the account's next charge day (a yearly account would get a year for one month's
    price; a purchase the day before a monthly charge would get one day)."""
    day = add_period(moment.date(), "monthly", moment.day)
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


# /P5 plans


def _insufficient(available: int, price: int, currency: str) -> APIError:
    return APIError(
        409,
        "The balance cannot cover this add-on without going past the pause point.",
        "insufficient_balance",
        available_minor=available,
        price_minor=price,
        currency=currency,
    )


async def apply_add_on(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    body: ApplyAddOnRequest,
    *,
    request: "Request | None",
    now: datetime | None = None,
) -> tuple[AddOnGrant, bool]:
    """Apply an add-on to a workspace; returns `(grant, created)` (`created` False: the same
    request was already applied). Locks the billing account, so two clicks at once end with one
    grant and one ledger entry. The caller commits, then calls `hooks.after_balance_change`
    when a money entry was written (`grant.entry_id`).

    Raises:
        TenantNotFoundError, APIError 404 `add_on_not_found`, 409 `billing_exempt`,
        `add_on_inactive`, `currency_mismatch`, `insufficient_balance`, `idempotency_conflict`;
        RequestValidationError: a credit pack cannot be paid from the balance.
    """
    return await _apply(
        session,
        tenant_id,
        add_on_id=body.add_on_id,
        payment=body.payment,
        memo=body.memo,
        idempotency_key=body.idempotency_key,
        request=request,
        now=now,
    )


# P1 payments
@dataclass(frozen=True, slots=True)
class GatewayPayment:
    """The online payment that pays for an add-on: its id, and the ledger key the payment's one
    entry carries (`rzp:<payment id>`)."""

    provider_payment_id: str
    entry_key: str


async def apply_paid_add_on(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    add_on_id: uuid.UUID,
    *,
    gateway: GatewayPayment,
    price_minor: int,
    actor: Actor | None = None,
    now: datetime | None = None,
) -> tuple[AddOnGrant, bool]:
    """Apply an add-on whose price just arrived through the payment gateway (`capture.py`).

    A credit pack's one entry is the pack itself (credit and bonus, keyed by the payment). Any
    other add-on is bought from the balance, which the payment just topped up, so the purchase is
    *funded*: it is never refused for `insufficient_balance`, even when the balance is negative.
    The grant id comes from the payment, so a replay is one grant. The caller commits.

    `price_minor` is what the payment topped up (the order's taxable part), not the add-on's
    current price: with GST-inclusive prices, or a price edited after the order, the two differ,
    and the top-up and the purchase must cancel exactly.
    """
    return await _apply(
        session,
        tenant_id,
        add_on_id=add_on_id,
        payment="gateway",
        memo=f"Paid online ({gateway.provider_payment_id})",
        idempotency_key=f"rzp-{gateway.provider_payment_id}",
        request=None,
        actor=actor,
        gateway=gateway,
        charge_minor=price_minor,
        now=now,
    )


# /P1 payments


async def _apply(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    add_on_id: uuid.UUID,
    payment: str,
    memo: str | None,
    idempotency_key: str,
    request: "Request | None",
    actor: Actor | None = None,
    gateway: "GatewayPayment | None" = None,
    charge_minor: int | None = None,
    now: datetime | None = None,
) -> tuple[AddOnGrant, bool]:
    moment = now or datetime.now(UTC)
    await accounts.account_for_update(session, tenant_id)
    ctx = await accounts.load_context(session, tenant_id)
    if ctx.effective.exempt:
        raise APIError(
            409,
            "This is your own workspace (or an internal one): it is never billed, so it takes "
            "no add-ons.",
            "billing_exempt",
        )
    grant_id = grant_id_for(tenant_id, idempotency_key)
    existing = await session.get(AddOnGrant, grant_id)
    if existing is not None:
        if existing.add_on_id != add_on_id or existing.payment != payment:
            raise ledger.IdempotencyConflict(idempotency_key)
        return existing, False
    add_on = await get_add_on(session, add_on_id)
    if not add_on.is_active:
        raise APIError(409, "That add-on is deactivated.", "add_on_inactive")
    currency = ctx.effective.currency
    if add_on.currency != currency:
        raise APIError(
            409,
            f"This customer is billed in {currency}; the add-on is priced in {add_on.currency}.",
            "currency_mismatch",
            account_currency=currency,
            currency=add_on.currency,
        )
    if add_on.kind == "credit_pack" and payment == "wallet":
        raise _invalid("A credit pack cannot be paid from the balance it adds to.")
    account = ctx.account
    # P1 payments: a gateway payment funds its own purchase, so only the wallet is checked
    pays_from_wallet = payment in ("wallet", "gateway") and add_on.price_minor > 0
    if payment == "wallet" and add_on.price_minor > 0:
        # /P1 payments
        fields = ctx.effective.fields
        available = await ledger.available_minor(session, account)
        if past_the_pause_line(fields, available, add_on.price_minor):  # P5 plans
            raise _insufficient(available, add_on.price_minor, currency)

    ends_at = None
    if add_on.kind == "question_pack" or (
        add_on.kind == "extra_limit" and add_on.duration == "period"
    ):
        ends_at = next_month_start(moment)
    # P5 plans: a monthly unlock (and a renewing service) runs one full month
    if (add_on.kind == "feature_unlock" and add_on.duration == "period") or (
        add_on.kind == "service" and add_on.renews
    ):
        ends_at = period_end(moment)
    if add_on.kind == "feature_unlock":
        allowed, _source = await byok_status(session, tenant_id, now=moment)
        if allowed:
            raise APIError(
                409,
                "Your plan already includes this, so there is nothing to buy.",
                "feature_already_included",
            )
    # /P5 plans
    if actor is None:  # P1 payments
        actor = actor_from_request(request)
    entry_id: uuid.UUID | None = None
    amount: int | None = None
    if add_on.kind == "credit_pack" or pays_from_wallet:
        if account.ledger_seq == 0 and account.currency != currency:
            account.currency = currency  # until the first entry the plan's currency decides
        if add_on.kind == "credit_pack":
            credit = int(add_on.credit_minor or 0)
            amount = credit + pct_of(credit, add_on.bonus_pct)
            bonus = f" (+{add_on.bonus_pct} % bonus)" if add_on.bonus_pct else ""
            entry = ledger.NewEntry(
                ledger.EntryType.ADD_ON_PURCHASE,
                amount,
                gateway.entry_key if gateway is not None else f"addon:{grant_id}",  # P1 payments
                reference_type="add_on_grant",
                reference_id=str(grant_id),
                memo=(memo or "").strip() or None,
                memo_public=f"Credit pack {Money(credit, currency).format()}{bonus}",
                expires_at=(
                    moment + timedelta(days=add_on.expiry_days)
                    if add_on.expiry_days is not None
                    else None
                ),
            )
        else:
            charged = add_on.price_minor
            if gateway is not None and charge_minor is not None:
                charged = charge_minor  # P1 payments: cancels the payment's own top-up
            amount = -charged
            entry = ledger.NewEntry(
                ledger.EntryType.ADD_ON_PURCHASE,
                amount,
                f"addon:{grant_id}",
                reference_type="add_on_grant",
                reference_id=str(grant_id),
                memo=(memo or "").strip() or None,
                memo_public=f"Add-on: {add_on.name}"[:200],
            )
        [result] = await ledger.append_entries(
            session, account.id, [entry], actor=actor, now=moment
        )
        entry_id = result.entry_id
    grant = AddOnGrant(
        id=grant_id,
        tenant_id=tenant_id,
        add_on_id=add_on.id,
        **{f: getattr(add_on, f) for f in _SNAPSHOT},
        payment=payment,
        provider_payment_id=gateway.provider_payment_id
        if gateway is not None
        else None,  # P1 payments
        entry_id=entry_id,
        starts_at=moment,
        ends_at=ends_at,
        granted_by=actor[2],
        memo=(memo or "").strip() or None,
        created_at=moment,
    )
    session.add(grant)
    await session.flush()
    if add_on.kind == "service":  # P5 plans: a tracked order in the Support inbox
        await service_orders.open_for_grant(
            session, ctx.tenant, grant, owner_note=(memo or "").strip() or None, now=moment
        )
    details: dict[str, Any] = {
        "grant_id": str(grant_id),
        "add_on_id": str(add_on.id),
        "kind": add_on.kind,
        "payment": payment,
    }
    if entry_id is not None:
        details["entry_id"] = str(entry_id)
        details["amount_minor"] = amount
    await record_audit(
        session,
        request,
        "billing.add_on_granted",
        tenant_id=ctx.tenant.id,
        tenant_label=ctx.tenant.name,
        target_type="add_on_grant",
        target_id=str(grant_id),
        details=details,
        actor=actor,  # P1 payments
    )
    return grant, True


async def revoke_grant(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    grant_id: uuid.UUID,
    memo: str | None,
    *,
    request: "Request | None",
    now: datetime | None = None,
) -> AddOnGrant:
    """End a grant now (the caller commits). Money is never refunded automatically: add a
    refund or an adjustment if some is due.

    Raises:
        APIError 404 `grant_not_found` (also another workspace's), 409 `grant_not_revocable`
        (credit packs: correct the money with an adjustment), 409 `grant_already_revoked`.
    """
    moment = now or datetime.now(UTC)
    await accounts.account_for_update(session, tenant_id)
    grant = await session.get(AddOnGrant, grant_id)
    if grant is None or grant.tenant_id != tenant_id:
        raise APIError(404, "That grant does not exist.", "grant_not_found")
    if grant.kind == "credit_pack":
        raise APIError(
            409,
            "A credit pack cannot be revoked: correct the balance with an adjustment instead.",
            "grant_not_revocable",
        )
    if grant.revoked_at is not None:
        raise APIError(409, "That grant was already revoked.", "grant_already_revoked")
    actor = actor_from_request(request)
    grant.revoked_at = moment
    grant.revoked_by = actor[2]
    grant.revoke_memo = (memo or "").strip() or None
    if grant.ends_at is None or grant.ends_at > moment:
        grant.ends_at = moment
    await session.flush()
    tenant = (await accounts.load_context(session, tenant_id)).tenant
    await record_audit(
        session,
        request,
        "billing.grant_revoked",
        tenant_id=tenant.id,
        tenant_label=tenant.name,
        target_type="add_on_grant",
        target_id=str(grant_id),
        details={
            "grant_id": str(grant_id),
            "add_on_id": str(grant.add_on_id),
            "kind": grant.kind,
            "payment": grant.payment,
        },
    )
    return grant


# P5 plans
async def cancel_renewal(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    grant_id: uuid.UUID,
    *,
    request: "Request | None",
    now: datetime | None = None,
) -> AddOnGrant:
    """Stop a monthly add-on from renewing (the caller commits). It stays in force until its
    `ends_at`; no money is returned.

    Raises:
        APIError 404 `grant_not_found` (also another workspace's), 409 `add_on_not_renewing`
        (it does not renew, is already cancelled, revoked or ended).
    """
    moment = now or datetime.now(UTC)
    await accounts.account_for_update(session, tenant_id)
    grant = await session.get(AddOnGrant, grant_id)
    if grant is None or grant.tenant_id != tenant_id:
        raise APIError(404, "That purchase does not exist.", "grant_not_found")
    if (
        not grant.renews
        or grant.cancelled_at is not None
        or grant.revoked_at is not None
        or (grant.ends_at is not None and grant.ends_at <= moment)
    ):
        raise APIError(
            409,
            "That purchase does not renew, so there is nothing to cancel.",
            "add_on_not_renewing",
        )
    grant.cancelled_at = moment
    await session.flush()
    tenant = (await accounts.load_context(session, tenant_id)).tenant
    await record_audit(
        session,
        request,
        "billing.add_on_cancelled",
        tenant_id=tenant.id,
        tenant_label=tenant.name,
        target_type="add_on_grant",
        target_id=str(grant_id),
        details={"grant_id": str(grant_id), "add_on_id": str(grant.add_on_id)},
    )
    return grant


# /P5 plans
