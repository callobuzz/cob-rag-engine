"""Which payment provider is in force, or None when online payments are off.

Off means: provider `none`, keys missing or billing disabled. Tests
inject a fake through `app.state.payment_provider_override`, read first by `provider_for`; the
factory itself never builds a fake.
"""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.billing.config import billing_enabled
from callobuzz_rag.billing.payments import settings as payment_settings
from callobuzz_rag.billing.payments.base import PaymentProvider
from callobuzz_rag.billing.payments.razorpay import RazorpayProvider
from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)


def payment_provider(resolved: payment_settings.ResolvedSettings) -> PaymentProvider | None:
    """The provider for resolved settings, or None when payments are off or unusable."""
    if not resolved.configured:
        return None
    try:
        return RazorpayProvider(
            key_id=resolved.key_id,
            key_secret=resolved.key_secret,
            webhook_secret=resolved.webhook_secret,
        )
    except ValueError:
        logger.warning("The saved Razorpay key id is not a test or live key; payments are off.")
        return None


async def provider_for(app: Any, session: AsyncSession) -> PaymentProvider | None:
    """The provider to use in a request or a task, or None when online payments are off."""
    override = getattr(app.state, "payment_provider_override", None)
    if override is not None:
        return override  # type: ignore[no-any-return]
    return await provider_from_settings(app.state.settings, session)


async def provider_from_settings(
    settings: Settings, session: AsyncSession
) -> PaymentProvider | None:
    """Same, for code with no app (the worker): the stored row and the environment."""
    if not billing_enabled():
        return None
    return payment_provider(await payment_settings.load(session, settings))
