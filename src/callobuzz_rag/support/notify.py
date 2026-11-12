"""The e-mails of a support request, both ways (TV3). Queued only after the request is committed
and never allowed to fail it: a mail that cannot be queued is logged, and the request stays.

The provider's inbox is `branding.support_email`, else the super admin (`RAG_SUPERADMIN_EMAIL`).
A reply from the provider goes to the workspace's owners and to the request's author, but only
while the author is still a member of the workspace: a reply can carry account details, and a
person who left the team must not keep receiving them.
"""

import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.db.models.support import SupportMessage, SupportRequest
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember

logger = logging.getLogger(__name__)

EXPIRES_S = 7 * 24 * 3600
Enqueue = Callable[..., None]


def deployer_inbox(support_email: str | None, superadmin_email: str | None) -> str:
    """Where the provider's side is told: the support address, else the super admin."""
    return (support_email or "").strip() or (superadmin_email or "").strip()


async def notify_business_message(
    session: AsyncSession,
    row: SupportRequest,
    message: SupportMessage,
    tenant: Tenant,
    *,
    enqueue: Enqueue,
    support_email: str | None,
    superadmin_email: str | None,
    dashboard_url: str,
    first: bool,
) -> bool:
    """Tell the provider about a new request (`first`) or a follow-up. True when queued."""
    from callobuzz_rag.accounts.email.render import dashboard_link

    to = deployer_inbox(support_email, superadmin_email)
    if not to:
        logger.warning("Support request #%s has no inbox to be mailed to", row.number)
        return False
    context: dict[str, Any] = {
        "number": row.number,
        "request_subject": row.subject if first else f"Re: {row.subject}",
        "business_name": tenant.name,
        "from_email": row.created_by_email if first else message.author_email,
        "message": message.body,
        "page": row.page or "",
        "admin_url": dashboard_link(dashboard_url, f"/admin/support?request={row.id}"),
    }
    try:
        enqueue("support_request_new", to, context, expires_s=EXPIRES_S)
        return True
    except Exception:
        logger.warning("Support mail for #%s was not queued", row.number, exc_info=True)
        return False


async def _is_member(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID | None
) -> bool:
    """Whether `user_id` is a current member of the workspace (None: a deleted account or a
    request made with a key, who is not a person to write back to)."""
    if user_id is None:
        return False
    found = (
        await session.execute(
            select(TenantMember.user_id).where(
                TenantMember.tenant_id == tenant_id, TenantMember.user_id == user_id
            )
        )
    ).first()
    return found is not None


async def notify_reply(
    session: AsyncSession,
    row: SupportRequest,
    message: SupportMessage,
    tenant: Tenant,
    *,
    enqueue: Enqueue,
    product_name: str,
    dashboard_url: str,
) -> int:
    """Tell the request's author and the workspace's owners about the provider's reply."""
    from callobuzz_rag.accounts.email.render import dashboard_link
    from callobuzz_rag.accounts.lifecycle import owner_emails

    # LP3 landing: a website enquiry's author is a visitor, not a member, and the workspace is
    # the operator's own: the operator answers by the contact links in the inbox, nobody is mailed.
    if "landing_contact" in (row.context or {}):
        return 0
    # /LP3 landing
    recipients = [*await owner_emails(session, tenant.id)]
    if await _is_member(session, tenant.id, row.created_by_user_id):
        recipients.insert(0, row.created_by_email)
    seen: set[str] = set()
    sent = 0
    context: dict[str, Any] = {
        "number": row.number,
        "request_subject": row.subject,
        "product_name": product_name,
        "message": message.body,
        "request_url": dashboard_link(dashboard_url, f"/support?request={row.id}"),
    }
    for to in recipients:
        key = to.strip().lower()
        if not key or key in seen or "@" not in key:
            continue
        seen.add(key)
        try:
            enqueue("support_reply", to, context, expires_s=EXPIRES_S)
            sent += 1
        except Exception:
            logger.warning("Support reply mail for #%s was not queued", row.number, exc_info=True)
    return sent
