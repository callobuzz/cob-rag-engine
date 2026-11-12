"""The owner's monthly spending cap on usage charges (TV3).

`billing_accounts.spend_cap_minor` (NULL = no cap) limits what usage may charge in the calendar
month (UTC days, the month the usage meter uses). The fair-use gate enforces it (see
`fairuse/decision.py`, metric `spend_cap_per_month`); this module holds the plain rules the Plan
page, the route and the alerts share. Subscriptions and add-on purchases are never counted.
"""

from datetime import UTC, datetime
from typing import Any, Final, Literal

from callobuzz_rag.api.errors import APIError

NEAR_PERCENT: Final = 80

CapState = Literal["ok", "near", "reached"]


def cap_state(cap_minor: int | None, used_minor: int) -> CapState:
    """`reached` at or over the cap, `near` from 80 % of it, else `ok` (also with no cap)."""
    if cap_minor is None or cap_minor <= 0:
        return "ok"
    if used_minor >= cap_minor:
        return "reached"
    if used_minor * 100 >= cap_minor * NEAR_PERCENT:
        return "near"
    return "ok"


def cap_view(cap_minor: int | None, used_minor: int, max_minor: int | None) -> dict[str, Any]:
    """The Plan page's cap card."""
    return {
        "cap_minor": cap_minor,
        "used_minor": used_minor,
        "max_minor": max_minor,
        "state": cap_state(cap_minor, used_minor),
    }


async def set_cap(
    session: Any,
    ctx: Any,
    cap_minor: int | None,
    *,
    max_minor: int | None,
    request: Any,
) -> int | None:
    """Set (or, with None, remove) the workspace's monthly spending cap and audit it. Does not
    commit. Returns the previous cap.

    Raises:
        APIError 409 `spend_cap_not_billed` (the plan charges nothing, so there is nothing to
        cap), 422 `spend_cap_too_high` (above the provider's ceiling; extra `max_minor`).
    """
    from callobuzz_rag.accounts.audit import record_audit
    from callobuzz_rag.billing.enforcement import stored_status

    account = ctx.account
    if cap_minor is not None:
        if ctx.effective.exempt or stored_status(ctx.effective, account) == "not_billed":
            raise APIError(
                409,
                "Your plan does not charge for usage, so there is nothing to cap.",
                "spend_cap_not_billed",
            )
        if max_minor is not None and cap_minor > max_minor:
            raise APIError(
                422,
                "That is above the highest cap your provider allows.",
                "spend_cap_too_high",
                max_minor=max_minor,
            )
    previous: int | None = account.spend_cap_minor
    if previous == cap_minor:
        return previous
    account.spend_cap_minor = cap_minor
    account.updated_at = datetime.now(UTC)
    await session.flush()
    await record_audit(
        session,
        request,
        "billing.spend_cap_set",
        tenant_id=ctx.tenant.id,
        tenant_label=ctx.tenant.name,
        target_type="billing_account",
        target_id=str(account.id),
        details={
            "cap_minor": cap_minor,
            "previous_minor": previous,
            "currency": ctx.effective.currency,
        },
    )
    return previous
