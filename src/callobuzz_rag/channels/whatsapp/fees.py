"""Meta's per-message fees, passed through (TV6).

Meta bills the owner of the WhatsApp Business account. When the number sits on the **deployer's**
account (`meta_billing = "deployer"`, a number the deployer connected by hand), the deployer can
pass those fees on: each sent message writes one usage charge in the business's currency, at rates
the deployer types in (engine settings `whatsapp.rates_minor`, never built in), posted to the
ledger like any other usage. When the business pays Meta itself (Embedded Signup: its own payment
method on its own WABA) nothing is charged; and a plan that says `whatsapp_fees = "included"`
absorbs them.

Rules, each pinned by `tests/unit/test_whatsapp_fees.py`:

- charged only for a deployer's number AND a plan that says `pass_through`;
- one charge per message, keyed by the message id, so a repeated send of the same id is one charge;
- a zero rate writes nothing;
- the first `free_service_per_month` service replies of a number each month are free (their rows
  are written with amount 0 so they are counted); only service rows count toward the allowance
  (a row's category is stored in `usage_charges.model`), so a utility follow-up never uses it up;
- the allowance is read under the number's row lock, held to the end of the transaction that
  writes the charge, so two workers sending at once cannot both take the last free message;
- charged when the message is accepted by Meta, not on its `delivered` status: Meta's statuses
  are not guaranteed to arrive (a phone offline, a webhook retried for days) and a charge that
  depends on one would be lost or late; a message Meta accepts but cannot deliver is rare and
  is billed by Meta as well (it bills on delivery, but the deployer's rate is a flat estimate);
- the message itself is never affected: a fee that cannot be written is logged and dropped.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from callobuzz_rag.accounts.settings_store import WhatsAppEngineSettings, get_engine_settings
from callobuzz_rag.billing.charging import (
    NAMESPACE_BILLING,
    SOURCE_WHATSAPP_FEE,
    charge_pass_through,
)
from callobuzz_rag.billing.money import MICRO_PER_MINOR
from callobuzz_rag.channels.policy import plan_channels
from callobuzz_rag.channels.whatsapp.availability import plan_of
from callobuzz_rag.db.models.channel import ChannelConnection
from callobuzz_rag.db.models.usage_charge import UsageCharge

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.channels.whatsapp.outbound import Category, SendContext

logger = logging.getLogger(__name__)


def fee_request_id(wamid: str) -> uuid.UUID:
    """The charge's id: one per message, however often it is reported."""
    return uuid.uuid5(NAMESPACE_BILLING, f"wa-fee:{wamid}")


def fee_applies(meta_billing: str, fees_mode: str) -> bool:
    """Only a number on the deployer's own WhatsApp account, on a plan that passes fees on."""
    return meta_billing == "deployer" and fees_mode == "pass_through"


def rate_minor(engine: WhatsAppEngineSettings, currency: str, category: str) -> int:
    """What Meta charges per message of `category`, in minor units of `currency`: what the
    deployer set, 0 when nothing is set."""
    rates = engine.rates_minor.get(currency)  # type: ignore[call-overload]
    return int(getattr(rates, category, 0)) if rates is not None else 0


def fee_minor(*, rate: int, category: str, used_this_month: int, free_service: int) -> int:
    """The fee for one more message: 0 inside the free service allowance, else the rate."""
    if rate <= 0:
        return 0
    if category == "service" and used_this_month < free_service:
        return 0
    return rate


def month_start(moment: datetime) -> datetime:
    moment = moment.astimezone(UTC)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def used_this_month(session: "AsyncSession", corpus_id: uuid.UUID, moment: datetime) -> int:
    """How many service fee rows (free ones too) this number has this month: only service
    messages draw on the free service allowance."""
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(UsageCharge)
                .where(
                    UsageCharge.corpus_id == corpus_id,
                    UsageCharge.source == SOURCE_WHATSAPP_FEE,
                    UsageCharge.model == "service",
                    UsageCharge.created_at >= month_start(moment),
                )
            )
        ).scalar_one()
        or 0
    )


async def charge_send(
    session: "AsyncSession", ctx: "SendContext", wamid: str, category: "Category"
) -> None:
    """The `after_send` hook: charge Meta's fee for the message just sent, if one applies. Runs in
    the same transaction as the sent-message record; never raises."""
    if ctx.meta_billing != "deployer":  # cheapest check first: the business pays Meta itself
        return
    try:
        plan = await plan_of(session, ctx.tenant_id)
        if plan is None or not fee_applies(ctx.meta_billing, plan_channels(plan).whatsapp_fees):
            return
        engine = (await get_engine_settings(ctx.session_factory)).whatsapp
        now = ctx.clock()

        async def price(currency: str) -> int | None:
            """None: no fee at all (no rate set). 0: a free-allowance message, written as a
            zero-amount row so it is counted."""
            rate = rate_minor(engine, currency, category)
            if rate <= 0:
                return None
            used = 0
            if category == "service":
                # One sender at a time per number while the allowance is read and the row written
                await session.execute(
                    select(ChannelConnection.id)
                    .where(ChannelConnection.id == ctx.connection_id)
                    .with_for_update()
                )
                used = await used_this_month(session, ctx.corpus_id, now)
            fee = fee_minor(
                rate=rate,
                category=category,
                used_this_month=used,
                free_service=engine.free_service_per_month,
            )
            return fee * MICRO_PER_MINOR

        result = await charge_pass_through(
            session,
            request_id=fee_request_id(wamid),
            tenant_id=ctx.tenant_id,
            corpus_id=ctx.corpus_id,
            price_micro=price,
            source=SOURCE_WHATSAPP_FEE,
            model=category,
            at=now,
        )
        if result.status == "charged":
            logger.debug("WhatsApp fee charged for %s", wamid)
    except Exception:
        logger.warning("Could not charge the WhatsApp fee for %s", wamid, exc_info=True)
