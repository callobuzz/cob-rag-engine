"""Billing enforcement: is this workspace allowed to cost the operator money right now?

`billing_state` is the read-only decision (no locks): a prepaid workspace is paused once what it
can still spend (`available`, counting usage the minute posting has not written yet) is at or
below minus its grace; a postpaid one once it owes its credit limit. A BYOK answer on a plan
whose `byok_mode` is `free` is never billed, so never paused (D6). `pause_override` lets the
operator force a pause for any payer or never pause. The operator's own workspace, the system
workspace and the Internal plan are exempt.

`enforce_billing` sits next to `enforce_question_cap` at every answering route. It never
raises: the route decides what a paused workspace gets (the director's pause reply, or
`raise_if_paused` on the legacy paths). The admin principal is never refused.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing import ledger
from callobuzz_rag.billing.config import billing_enabled
from callobuzz_rag.billing.context import is_admin_request, mark_admin_answer
from callobuzz_rag.billing.money import Currency
from callobuzz_rag.billing.pricing import EffectivePricing, TenantNotFoundError, effective_pricing
from callobuzz_rag.db.models.billing_account import BillingAccount

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.db.models.corpus import Corpus

logger = logging.getLogger(__name__)

Reason = Literal["ok", "prepaid_empty", "credit_limit", "forced", "not_billed"]
Surface = Literal["api", "eval", "widget", "cob"]


@dataclass(frozen=True)
class BillingState:
    billed: bool  # False: billing off, exempt, no pricing, or not chargeable for this payer
    paused: bool
    reason: Reason
    available_minor: int | None  # floor(available_micro / 10^4); None when not billed
    currency: Currency | None


NOT_BILLED = BillingState(False, False, "not_billed", None, None)


async def _load_account(session: "AsyncSession", tenant_id: uuid.UUID) -> BillingAccount | None:
    return (
        await session.execute(select(BillingAccount).where(BillingAccount.tenant_id == tenant_id))
    ).scalar_one_or_none()


async def billing_state(
    session: "AsyncSession",
    tenant_id: uuid.UUID,
    *,
    payer: str,
    now: datetime | None = None,
) -> BillingState:
    """The workspace's billing state for an answer paid by `payer` (`operator` | `byok`).

    Read-only: no account is created and nothing is locked. A chargeable workspace without an
    account yet has balance 0."""
    del now  # the state is live; kept so callers and tests can pass a clock
    if not billing_enabled():
        return NOT_BILLED
    try:
        eff = await effective_pricing(session, tenant_id)
    except TenantNotFoundError:  # nothing to bill: the route's own lookups report the workspace
        return NOT_BILLED
    if eff.exempt:
        return NOT_BILLED
    account = await _load_account(session, tenant_id)
    available: int | None = None
    if account is not None and (eff.chargeable(payer) or account.pause_override == "force"):
        owed = await ledger.owed_micro(session, account)
        available = ledger.available_minor_of(int(account.balance_minor), owed)
    return decide(eff, account, available, payer)


def decide(
    eff: EffectivePricing,
    account: BillingAccount | None,
    available: int | None,
    payer: str,
) -> BillingState:
    """The pause rule, pure. `available` is what the workspace can still spend, counting usage
    not yet posted; None means no account (balance 0)."""
    override = account.pause_override if account is not None else "auto"
    billed = eff.chargeable(payer)
    spendable = 0 if available is None else available
    if override == "force":
        return BillingState(billed, True, "forced", spendable, eff.currency)
    if not billed:
        return BillingState(False, False, "not_billed", None, eff.currency)
    f = eff.fields
    if override == "never":
        return BillingState(True, False, "ok", spendable, eff.currency)
    if f.payment_type == "postpaid":
        if spendable <= -f.credit_limit_minor:
            return BillingState(True, True, "credit_limit", spendable, eff.currency)
    elif spendable <= -f.grace_minor:
        return BillingState(True, True, "prepaid_empty", spendable, eff.currency)
    return BillingState(True, False, "ok", spendable, eff.currency)


def account_state(
    eff: EffectivePricing, account: BillingAccount | None, available: int | None
) -> BillingState:
    """The workspace's state for notices and screens: as an operator-key payer, else (a plan that
    only bills BYOK use) as a BYOK payer. A forced pause shows for either."""
    state = decide(eff, account, available, "operator")
    if not state.billed:
        byok = decide(eff, account, available, "byok")
        if byok.billed or byok.paused:
            return byok
    return state


Status = Literal["active", "low", "paused", "forced", "not_billed"]


def stored_status(eff: EffectivePricing, account: BillingAccount | None) -> Status:
    """The admin lists' badge, from what the balance hook last stored (no query, at most a
    minute behind): `paused_at` and `alert_level` on the account."""
    if eff.exempt:
        return "not_billed"
    if account is not None and account.pause_override == "force":
        return "forced"
    if account is not None and account.paused_at is not None:
        return "paused"
    if not (eff.chargeable("operator") or eff.chargeable("byok")):
        return "not_billed"
    if account is not None and account.alert_level not in (None, 0):
        return "low"
    return "active"


def stored_reason(eff: EffectivePricing, account: BillingAccount | None) -> Reason | None:
    """Why a stored-paused account is paused (None when it is not)."""
    if account is None or eff.exempt:
        return None
    if account.pause_override == "force":
        return "forced"
    if account.paused_at is None:
        return None
    return "credit_limit" if eff.fields.payment_type == "postpaid" else "prepaid_empty"


async def enforce_billing(
    session: "AsyncSession",
    request: "Request",
    tenant_id: uuid.UUID,
    *,
    payer: str,
    surface: Surface,
) -> BillingState:
    """Mark the request as admin or customer for the charge, then return the state. Never
    raises. The admin principal is never paused."""
    del surface  # the routes decide what each surface does with a paused state
    if mark_admin_answer(request) or is_admin_request(request):
        return NOT_BILLED
    try:
        return await billing_state(session, tenant_id, payer=payer)
    except Exception:
        # A billing read must never take a customer's answers down: fail open, say so.
        logger.warning("Billing state unavailable for %s; answering", tenant_id, exc_info=True)
        return NOT_BILLED


async def raise_if_paused(state: BillingState, request: "Request") -> None:
    """The legacy answer path has no pause message to give: 402 `billing_paused` (no amounts;
    a `ck_` key may sit in a customer's app)."""
    if not state.paused:
        return
    from callobuzz_rag.accounts.caps import _support_contact

    contact = await _support_contact(request)
    raise APIError(
        402,
        f"Answers are paused because this workspace's balance has run out — contact {contact}.",
        "billing_paused",
        reason=state.reason,
    )


def payer_hint(
    corpus: "Corpus",
    request: "Request | None",
    *,
    use_headers: bool,
    byok_allowed: bool,  # P5 plans: required, so no call site can forget the plan
) -> str:
    """`byok` when the knowledge base stores its own LLM key, or (on routes that take
    `X-LLM-*`) the request carries one, and the plan lets the business use its own key
    (`billing/byok.py`, `EffectiveProfile.byok_allowed`); else `operator`."""
    if not byok_allowed:
        return "operator"
    config: Any = corpus.llm_config
    if isinstance(config, dict) and config.get("api_key_encrypted"):
        return "byok"
    if use_headers and request is not None and request.headers.get("x-llm-api-key"):
        return "byok"
    return "operator"
