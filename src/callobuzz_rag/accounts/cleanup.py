"""Daily cleanup of account leftovers (accounts A4; beat task `cleanup_accounts`, 04:00 UTC).

- e-mailed codes expired more than a day ago;
- sessions revoked or expired more than 7 days ago (kept a week for "your sessions" and
  incident review);
- abandoned sign-ups: users who never verified their address, created more than 7 days
  ago, with no workspace membership, never the super admin;
- invitations (accounts A5) expired, revoked or accepted more than 30 days ago;
- COB conversations (COB2) not touched for `RAG_COB_HISTORY_DAYS` days (their messages go
  with them), the same window the history list shows;
- frozen value reports (TV4) older than the retention (24 months, plus one of slack so a
  time zone behind UTC never deletes a month the hourly job would freeze again): they hold what
  visitors asked.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import delete, exists, or_, select

from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.email_code import EmailCode
from callobuzz_rag.db.models.invitation import Invitation
from callobuzz_rag.db.models.support import SupportRequest  # LP3 landing
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User
from callobuzz_rag.db.models.user_session import UserSession
from callobuzz_rag.db.models.value import ValueReport

logger = logging.getLogger(__name__)

CODE_GRACE: Final = timedelta(days=1)
SESSION_GRACE: Final = timedelta(days=7)
ABANDONED_SIGNUP_AFTER: Final = timedelta(days=7)
INVITATION_GRACE: Final = timedelta(days=30)
DEFAULT_COB_HISTORY_DAYS: Final = 30
DEFAULT_LANDING_CONTACT_KEEP_DAYS: Final = 365  # LP3 landing


def _count(result: Any) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


async def cleanup(
    session: Any,
    *,
    now: datetime | None = None,
    cob_history_days: int = DEFAULT_COB_HISTORY_DAYS,
    landing_contact_keep_days: int = DEFAULT_LANDING_CONTACT_KEEP_DAYS,  # LP3 landing
) -> dict[str, int]:
    """Delete the leftovers in `session` and commit. Returns how many of each."""
    now = now or datetime.now(UTC)
    codes = await session.execute(delete(EmailCode).where(EmailCode.expires_at < now - CODE_GRACE))
    sessions = await session.execute(
        delete(UserSession).where(
            or_(
                UserSession.revoked_at < now - SESSION_GRACE,
                UserSession.expires_at < now - SESSION_GRACE,
            )
        )
    )
    users = await session.execute(
        delete(User).where(
            User.email_verified_at.is_(None),
            User.created_at < now - ABANDONED_SIGNUP_AFTER,
            User.is_superadmin.is_(False),
            ~exists(select(TenantMember.user_id).where(TenantMember.user_id == User.id)),
        )
    )
    invitation_cutoff = now - INVITATION_GRACE
    invitations = await session.execute(
        delete(Invitation).where(
            or_(
                Invitation.expires_at < invitation_cutoff,
                Invitation.revoked_at < invitation_cutoff,
                Invitation.accepted_at < invitation_cutoff,
            )
        )
    )
    cob = await session.execute(  # COB2: private chats with COB, past their retention
        delete(Conversation).where(
            Conversation.kind == "cob",
            Conversation.updated_at < now - timedelta(days=cob_history_days),
        )
    )
    from callobuzz_rag.business.value_report import RETENTION_MONTHS

    month = now.date().replace(day=1)
    for _ in range(RETENTION_MONTHS + 1):
        month = (month - timedelta(days=1)).replace(day=1)
    reports = await session.execute(  # TV4: frozen reports keep what visitors asked
        delete(ValueReport).where(ValueReport.month < month)
    )
    # LP3 landing: website enquiries hold a visitor's name and contact details, so they go after
    # `RAG_LANDING_CONTACT_KEEP_DAYS` (last touched), never a workspace's own support request.
    # Found by the marker only the contact form writes (`context.landing_contact`) on the
    # operator workspace; `page` is caller-supplied, so it proves nothing.
    from callobuzz_rag.accounts.operator import get_operator_tenant

    operator = await get_operator_tenant(session)
    enquiries = None
    if operator is not None:
        enquiries = await session.execute(
            delete(SupportRequest).where(
                SupportRequest.tenant_id == operator.id,
                SupportRequest.context.has_key("landing_contact"),
                SupportRequest.last_message_at < now - timedelta(days=landing_contact_keep_days),
            )
        )
    # /LP3 landing
    await session.commit()
    return {
        "email_codes": _count(codes),
        "sessions": _count(sessions),
        "abandoned_signups": _count(users),
        "invitations": _count(invitations),
        "cob_conversations": _count(cob),
        "value_reports": _count(reports),
        "landing_contacts": _count(enquiries) if enquiries is not None else 0,  # LP3 landing
    }


def _cob_history_days() -> int:
    from callobuzz_rag.core.config import get_settings

    return int(get_settings().cob_history_days)


def _landing_keep_days() -> int:  # LP3 landing
    from callobuzz_rag.core.config import get_settings

    return int(get_settings().landing_contact_keep_days)


async def run(session_factory: Callable[[], Any] | None = None) -> dict[str, int]:
    """The beat task's body: its own engine unless a session factory is given."""
    engine = None
    if session_factory is None:
        from callobuzz_rag.core.config import get_settings
        from callobuzz_rag.db.engine import create_engine, create_session_factory

        engine = create_engine(get_settings())
        session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            counts = await cleanup(
                session,
                cob_history_days=_cob_history_days(),
                landing_contact_keep_days=_landing_keep_days(),  # LP3 landing
            )
    finally:
        if engine is not None:
            await engine.dispose()
    logger.info(
        "Account cleanup: %d codes, %d sessions, %d abandoned sign-ups, %d invitations, "
        "%d COB conversations, %d value reports deleted",
        counts["email_codes"],
        counts["sessions"],
        counts["abandoned_signups"],
        counts["invitations"],
        counts["cob_conversations"],
        counts["value_reports"],
    )
    return counts
