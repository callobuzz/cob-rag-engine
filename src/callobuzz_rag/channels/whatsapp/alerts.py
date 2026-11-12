"""One-time e-mails to a workspace's owners about its WhatsApp number (TV6).

Two transitions need a person: Meta stops accepting the access token (the connection goes to
`error`, `last_error_code = "token"`), and the plan loses WhatsApp (the connection is
`disabled`, `last_error_code = "plan"`). Each is a state change claimed with a conditional UPDATE
(`claim_*` in `inbound.py`), so the mail goes once per transition, never once per message, and
only after that update has committed. Best effort: a mail that cannot be queued is logged.
"""

import logging
import uuid
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

NOTICE_EXPIRES_S = 24 * 3600


async def notify_owners(
    session_factory: Callable[[], Any],
    settings: Any,
    *,
    tenant_id: uuid.UUID,
    number: str,
    reason: str,
    enqueue: Callable[..., None] | None = None,
) -> int:
    """Queue `whatsapp_attention` for every owner of the workspace; how many were queued."""
    from callobuzz_rag.accounts.email.render import dashboard_link
    from callobuzz_rag.accounts.lifecycle import owner_emails
    from callobuzz_rag.db.models.tenant import Tenant

    try:
        async with session_factory() as session:
            tenant = await session.get(Tenant, tenant_id)
            owners = await owner_emails(session, tenant_id)
        if tenant is None or not owners:
            return 0
        if enqueue is None:
            from callobuzz_rag.accounts.email.dispatch import enqueue_email

            enqueue = enqueue_email
        context = {
            "tenant_name": tenant.name,
            "number": number,
            "reason": reason,
            "whatsapp_url": dashboard_link(settings.dashboard_url, "/whatsapp"),
        }
    except Exception:
        logger.warning("Could not prepare the WhatsApp owner mail", exc_info=True)
        return 0
    sent = 0
    for to in owners:
        try:
            enqueue("whatsapp_attention", to, context, expires_s=NOTICE_EXPIRES_S)
            sent += 1
        except Exception as exc:
            logger.error("WhatsApp owner mail not queued (%s)", type(exc).__name__)
    return sent
