"""The two demo sign-ins: the platform owner and the demo business's owner.

Written directly, like `accounts/bootstrap.py` writes the super admin: a user row has no route
that takes a known password, and the demo's password is public by design. Everything else the
reset builds goes through the engine's own routes. Neither function commits; the reset does.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from callobuzz_rag.accounts.passwords import hash_password, normalize_email
from callobuzz_rag.core.exceptions import RAGKernelError
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

OWNER_ROLE = "owner"  # the role value `accounts/members._join` stores for an owner


class DemoSetupError(RAGKernelError):
    """The demo cannot set up its sign-ins (for example, the super admin does not exist yet)."""


async def _find(session: "AsyncSession", email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


async def prepare_operator(session: "AsyncSession", email: str, password: str) -> User:
    """Give the super admin the demo password and mark the address verified.

    The super admin row is created by the bootstrap that runs at every start; the demo never
    creates it.

    Raises:
        DemoSetupError: there is no such user (the bootstrap has not run).
    """
    user = await _find(session, email)
    if user is None:
        raise DemoSetupError(
            "The platform owner does not exist yet: run the bootstrap first "
            "(it runs when the server starts), then the demo reset."
        )
    now = datetime.now(UTC)
    user.password_hash = await hash_password(password)
    user.password_changed_at = now
    user.email_verified_at = user.email_verified_at or now
    user.is_active = True
    return user


async def ensure_owner(
    session: "AsyncSession", tenant_id: uuid.UUID, email: str, name: str, password: str
) -> User:
    """Create (or refresh) the demo business owner and make them the workspace's one owner.

    Never a super admin. Calling it twice changes nothing but the password hash.
    """
    now = datetime.now(UTC)
    user = await _find(session, email)
    if user is None:
        user = User(email=normalize_email(email), name=name, is_active=True, is_superadmin=False)
        session.add(user)
        await session.flush()
    user.name = name
    user.is_superadmin = False
    user.is_active = True
    user.password_hash = await hash_password(password)
    user.password_changed_at = now
    user.email_verified_at = user.email_verified_at or now
    members = (
        (await session.execute(select(TenantMember).where(TenantMember.tenant_id == tenant_id)))
        .scalars()
        .all()
    )
    if not any(m.user_id == user.id for m in members):
        session.add(TenantMember(tenant_id=tenant_id, user_id=user.id, role=OWNER_ROLE))
        await session.flush()
    return user
