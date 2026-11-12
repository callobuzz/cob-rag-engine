"""Create the super-admin account and send its set-password code.

    python -m callobuzz_rag.accounts.bootstrap [--resend] [--print-link]

Run by the container entrypoint on every start (a no-op once the password is set) and by
`make bootstrap`. `RAG_SUPERADMIN_EMAIL` is the source of truth:

0. whatever happens below, the operator's own workspace is ensured (created once, with the
   super admin as its owner; with no address configured it has no members): see
   `accounts/operator.py`. A new RAG_SUPERADMIN_EMAIL becomes an owner of the existing
   workspace; a demoted super admin is taken out of it. A workspace the operator deleted on
   purpose stays deleted unless `--restore-operator-workspace` is given.
1. no RAG_SUPERADMIN_EMAIL -> nothing else to do (exit 0)
2. accounts off (no RAG_SESSION_SECRET) -> exit 1
3. every OTHER user flagged super admin is demoted and their sessions revoked
4. the address belongs to a workspace member -> refuse (exit 2); it has a password and no
   --resend -> "super admin ready" (exit 0)
5. otherwise create the account if needed (no password), issue a 24-hour
   `superadmin_setup` code and either e-mail it (SMTP, sent now: the mailer may not be up
   yet at container start) or print the one-time link and code to this console.

Printing happens without SMTP even in production, deliberately: it is the recovery path
the design calls for, it goes to the operator's own console, and the code is single-use
and short-lived. The `LogEmailProvider` (which would put codes in application logs) stays
refused in production. The link carries the code in its #fragment, which browsers never
send to a server. The operator sets the password with POST /api/v1/auth/reset (the
dashboard's /set-password page does it).
"""

import argparse
import asyncio
import logging
import sys
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select

from callobuzz_rag.accounts.codes import TTL_S, issue_code
from callobuzz_rag.accounts.passwords import normalize_email
from callobuzz_rag.accounts.sessions import revoke_all
from callobuzz_rag.accounts.tokens import configured_secrets
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
EXIT_REFUSED: Final = 2
BANNER: Final = "ONE-TIME SET-PASSWORD LINK (valid 24 h)"
SYSTEM_ACTOR: Final = ("system", None, "super-admin bootstrap")

Out = Callable[[str], None]


def console(line: str) -> None:
    """The operator's console (stdout), not the log: the link must reach the operator even
    when logging is filtered, and must not be copied into log aggregation."""
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


async def _other_superadmins(session: "AsyncSession", email: str) -> list[User]:
    result = await session.execute(
        select(User).where(User.is_superadmin.is_(True), User.email != email)
    )
    return list(result.scalars().all())


async def _find_user(session: "AsyncSession", email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def _is_member(session: "AsyncSession", user_id: uuid.UUID) -> bool:
    """A member of a customer workspace (the operator's own workspace does not count: the
    super admin is its owner)."""
    result = await session.execute(
        select(TenantMember.tenant_id)
        .join(Tenant, Tenant.id == TenantMember.tenant_id)
        .where(TenantMember.user_id == user_id, Tenant.is_operator.is_(False))
        .limit(1)
    )
    return result.first() is not None


async def _workspace(
    session: "AsyncSession",
    owner: User | None,
    demoted: list[uuid.UUID],
    *,
    restore: bool,
) -> bool:
    """Ensure the operator workspace (with `owner`, the super admin to be); True when it
    added `owner` to an existing workspace."""
    from callobuzz_rag.accounts.operator import ensure_operator_workspace, remove_memberships

    await remove_memberships(session, demoted)
    result = await ensure_operator_workspace(session, owner=owner, restore=restore)
    return result.owner_added and not result.created


async def _audit(session: "AsyncSession", user: User | None, details: dict[str, Any]) -> None:
    from callobuzz_rag.accounts.audit import record_audit

    await record_audit(
        session,
        None,
        "superadmin.bootstrapped",
        target_type="user",
        target_id=str(user.id) if user is not None else None,
        details=details,
        actor=SYSTEM_ACTOR,
    )


async def run(
    settings: "Settings",
    session_factory: Callable[[], Any],
    *,
    resend: bool = False,
    print_link: bool = False,
    restore_operator_workspace: bool = False,
    out: Out = console,
) -> int:
    """The bootstrap; returns the process exit code."""
    restore = restore_operator_workspace
    if not settings.superadmin_email:
        async with session_factory() as session:
            await _workspace(session, None, [], restore=restore)
            await session.commit()
        out("No RAG_SUPERADMIN_EMAIL; skipping")
        return EXIT_OK
    if not configured_secrets(settings):
        # Also with RAG_DEBUG: the API's random per-process secret could not verify a
        # code issued here with another one.
        out(
            "Accounts are disabled: RAG_SESSION_SECRET is not set "
            "(run `make setup`, or python scripts/generate_secrets.py, to fill it). "
            "Super admin not bootstrapped."
        )
        return EXIT_FAILED

    email = normalize_email(settings.superadmin_email)
    async with session_factory() as session:
        demoted: list[str] = []
        demoted_ids: list[uuid.UUID] = []
        for other in await _other_superadmins(session, email):
            other.is_superadmin = False
            await revoke_all(session, other.id, "revoked")
            demoted.append(other.email)
            demoted_ids.append(other.id)

        user = await _find_user(session, email)
        if user is not None and await _is_member(session, user.id):
            await _workspace(session, None, demoted_ids, restore=restore)
            if demoted:
                await _audit(session, user, {"demoted": demoted, "refused": True})
            await session.commit()
            out(f"Refusing: {email} belongs to a workspace; choose another RAG_SUPERADMIN_EMAIL")
            return EXIT_REFUSED

        if user is not None and user.password_hash and not resend:
            changed = not user.is_superadmin
            user.is_superadmin = True
            owner_added = await _workspace(session, user, demoted_ids, restore=restore)
            if demoted or changed or owner_added:
                ready: dict[str, Any] = {"demoted": demoted, "promoted": changed}
                if owner_added:
                    ready["operator_workspace_owner_added"] = True
                await _audit(session, user, ready)
            await session.commit()
            out(f"Super admin ready: {email}")
            return EXIT_OK

        created = user is None
        if user is None:
            user = User(email=email, name="Super admin", is_superadmin=True, is_active=True)
            session.add(user)
            await session.flush()
        else:
            user.is_superadmin = True
        owner_added = await _workspace(session, user, demoted_ids, restore=restore)
        code = await issue_code(
            session, settings=settings, email=email, purpose="superadmin_setup", user_id=user.id
        )
        details: dict[str, Any] = {"created": created, "setup_code_issued": True}
        if demoted:
            details["demoted"] = demoted
        if owner_added:
            details["operator_workspace_owner_added"] = True
        await _audit(session, user, details)
        await session.commit()

    from callobuzz_rag.accounts.email.factory import email_provider_from_settings
    from callobuzz_rag.accounts.email.render import dashboard_link

    link = dashboard_link(settings.dashboard_url, "set-password", {"email": email, "code": code})
    hours = TTL_S["superadmin_setup"] // 3600
    try:
        provider = email_provider_from_settings(settings)
    except RuntimeError as exc:  # misconfigured e-mail: the console is the way out
        out(f"E-mail is misconfigured ({exc}); printing the link instead.")
        provider = None
    if provider is not None and provider.name == "smtp" and not print_link:
        from callobuzz_rag.accounts.email.dispatch import send_now

        try:
            await send_now(
                "superadmin_setup",
                email,
                {"code": code, "link": link, "hours": hours},
                settings,
                session_factory,
                provider=provider,
            )
        except Exception as exc:
            smtp_code = getattr(exc, "smtp_code", None)
            out(
                f"Could not e-mail the set-password code to {email}: {type(exc).__name__}"
                + (f" (SMTP {smtp_code})" if smtp_code else "")
            )
            out("Run again with --resend, or with --print-link to print it here instead.")
            return EXIT_FAILED
        out(f"Set-password e-mail sent to {email} (valid {hours} h)")
        return EXIT_OK

    bar = "=" * 72
    out(bar)
    out(BANNER)
    out(f"  account: {email}")
    out(f"  link:    {link}")
    out(f"  code:    {code}")
    out("Open the link, or POST /api/v1/auth/reset with the e-mail, code and a new password.")
    out(bar)
    return EXIT_OK


async def _main(resend: bool, print_link: bool, restore: bool) -> int:
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.core.logging import configure_logging
    from callobuzz_rag.db.engine import create_engine, create_session_factory

    configure_logging("WARNING")
    settings = get_settings()
    engine = create_engine(settings)
    try:
        return await run(
            settings,
            create_session_factory(engine),
            resend=resend,
            print_link=print_link,
            restore_operator_workspace=restore,
        )
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the super admin (RAG_SUPERADMIN_EMAIL).")
    parser.add_argument(
        "--resend", action="store_true", help="issue a new set-password code even if set"
    )
    parser.add_argument(
        "--print-link", action="store_true", help="print the link here instead of e-mailing it"
    )
    parser.add_argument(
        "--restore-operator-workspace",
        action="store_true",
        help="recreate the operator's own workspace even though it was deleted on purpose",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_main(args.resend, args.print_link, args.restore_operator_workspace))


if __name__ == "__main__":
    sys.exit(main())
