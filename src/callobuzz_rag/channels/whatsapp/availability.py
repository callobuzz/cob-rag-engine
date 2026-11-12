"""Is WhatsApp on: the engine's Meta app, and a business's plan (TV6).

The engine is ready when `RAG_WHATSAPP_ENABLED` is true and the app id, app secret and webhook
verify token are all set (the settings validator already refuses `enabled` without them; this
repeats the check so a half-built `Settings` object never reads as ready). Embedded Signup also
needs the configuration id. A business may use it when its plan says so; the operator's own
workspace (the Internal plan) always may.
"""

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from callobuzz_rag.channels.policy import plan_channels
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def engine_ready(settings: Any) -> bool:
    return bool(
        getattr(settings, "whatsapp_enabled", False)
        and str(settings.whatsapp_app_id).strip()
        and settings.whatsapp_app_secret.get_secret_value().strip()
        and settings.whatsapp_verify_token.get_secret_value().strip()
    )


def embedded_ready(settings: Any) -> bool:
    return engine_ready(settings) and bool(str(settings.whatsapp_config_id).strip())


async def plan_of(session: "AsyncSession", tenant_id: uuid.UUID) -> Plan | None:
    """The tenant's plan."""
    return (
        await session.execute(
            select(Plan).join(Tenant, Tenant.plan_id == Plan.id).where(Tenant.id == tenant_id)
        )
    ).scalar_one_or_none()


async def plan_allows(session: "AsyncSession", tenant_id: uuid.UUID) -> bool:
    """Whether the tenant's plan includes WhatsApp."""
    plan = await plan_of(session, tenant_id)
    if plan is None:
        return False
    return bool(plan.is_internal or plan_channels(plan).whatsapp)


# OS2 public address
def public_address_ok(settings: Any) -> bool:
    """WhatsApp needs a public https address: Meta cannot reach a development address."""
    from callobuzz_rag.core.install import install_domain, is_dev_domain

    return not is_dev_domain(install_domain(settings))


# /OS2 public address
