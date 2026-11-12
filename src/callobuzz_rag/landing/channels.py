"""Whether the website may talk about WhatsApp (LP1, decision D8).

Only when at least one active, non-internal plan includes the channel: a deployer who does not
sell it must not advertise it.
"""

from typing import TYPE_CHECKING

from sqlalchemy import select

from callobuzz_rag.channels.policy import plan_channels
from callobuzz_rag.db.models.plan import Plan

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def whatsapp_on_sale(session: "AsyncSession") -> bool:
    plans = (await session.execute(select(Plan))).scalars().all()
    return any(
        plan.is_active and not plan.is_internal and plan_channels(plan).whatsapp for plan in plans
    )
