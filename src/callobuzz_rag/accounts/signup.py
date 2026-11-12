"""Sign-up, e-mail verification, resend and forgot-password (accounts A4).

Sign-up creates (or, for an address that never verified, overwrites) an unverified user
and e-mails a 6-digit `verify` code whose payload holds the business name and the
sign-up mode in force. The tenant is created only at verification, so abandoned sign-ups
leave no half-made workspace; the mode stored with the code decides `active` (open) or
`pending` (approval), whatever the operator switched to meanwhile.

No enumeration: sign-up, resend and forgot answer every address with the same status
and body. A sign-up on a verified address (or the super admin's) sends an
`account_exists` mail instead of a code, and the password is hashed in every branch so
the time taken matches.
"""

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select

from callobuzz_rag.accounts import service
from callobuzz_rag.accounts.codes import consume_code, issue_code
from callobuzz_rag.accounts.email.factory import email_configured
from callobuzz_rag.accounts.email_rules import EmailRuleError, check_email
from callobuzz_rag.accounts.lifecycle import PendingEmails, create_signup_tenant
from callobuzz_rag.accounts.passwords import hash_password, normalize_email, validate_password
from callobuzz_rag.accounts.ratelimit import AccountRateLimiter
from callobuzz_rag.accounts.sessions import create_session, request_ip
from callobuzz_rag.accounts.settings_store import get_engine_settings
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.core.exceptions import EmailError
from callobuzz_rag.db.models.email_code import EmailCode
from callobuzz_rag.db.models.user import User

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

# (bucket, limit, window seconds). Resend and forgot share the `code_mail` buckets.
SIGNUP_IP_LIMIT: Final = ("signup_ip", 5, 3600)
SIGNUP_EMAIL_LIMIT: Final = ("signup_email", 3, 3600)
VERIFY_EMAIL_LIMIT: Final = ("verify_email", 10, 600)
VERIFY_IP_LIMIT: Final = ("verify_ip", 30, 600)
CODE_MAIL_EMAIL_MINUTE: Final = ("code_mail_email_1m", 1, 60)
CODE_MAIL_EMAIL_HOUR: Final = ("code_mail_email_1h", 5, 3600)
CODE_MAIL_IP_LIMIT: Final = ("code_mail_ip", 20, 3600)
CODE_MINUTES: Final = 10

SENT_IF_APPLICABLE: Final = {"status": "sent_if_applicable"}


async def _hit(limiter: AccountRateLimiter, limit: tuple[str, int, int], identity: str) -> None:
    await limiter.hit(limit[0], identity, limit[1], limit[2])


def _email_not_configured() -> APIError:
    return APIError(503, "E-mail is not configured on this engine", "email_not_configured")


def _email_unavailable() -> APIError:
    return APIError(
        503, "We could not send the e-mail right now. Try again shortly.", "email_unavailable"
    )


def _send(emails: PendingEmails) -> None:
    """Queue mail a person is waiting for; a broker failure is 503 `email_unavailable`."""
    try:
        emails.send(strict=True)
    except EmailError as exc:
        raise _email_unavailable() from exc


def _is_superadmin_address(email: str, settings: "Settings") -> bool:
    return bool(settings.superadmin_email) and email == normalize_email(settings.superadmin_email)


def _link(settings: "Settings", path: str) -> str:
    from callobuzz_rag.accounts.email.render import dashboard_link

    return dashboard_link(settings.dashboard_url, path)


async def signup(
    session: "AsyncSession",
    request: "Request",
    limiter: AccountRateLimiter,
    *,
    business_name: str,
    name: str,
    email: str,
    password: str,
    accept_legal: dict[str, int] | None = None,  # P3 legal
    plan: str | None = None,  # P6 plan choice
) -> dict[str, Any]:
    """Start a sign-up. Returns `{"status": "verification_sent", "email": ...}` for every
    address (new, unverified or verified)."""
    settings: Settings = request.app.state.settings
    email = normalize_email(email)
    engine = await get_engine_settings(request.app.state.session_factory)
    if engine.signup_mode == "closed":
        raise APIError(
            403,
            engine.branding.contact_text,
            "signup_closed",
            contact_url=engine.branding.contact_url,
        )
    try:
        check_email(email, allow_special_use=not settings.is_prod)
    except EmailRuleError as exc:
        raise APIError(422, str(exc), "invalid_email") from exc
    if not email_configured(settings):
        raise _email_not_configured()
    # P3 legal: the Terms and Privacy versions the person accepted, when both are published.
    # Checked before a code is mailed; it does not depend on the address, so it reveals nothing
    from callobuzz_rag.legal import service as legal_service

    accepted_legal = legal_service.check_acceptance(
        await legal_service.required_versions(session), accept_legal
    )
    # /P3 legal
    # P6 plan choice: only a plan businesses may choose themselves; anything else (unknown, not
    # self-service, another deployer's id) is the default plan, silently
    from callobuzz_rag.billing.plan_change import resolve_ref

    chosen = await resolve_ref(session, plan)
    # /P6 plan choice
    reasons = validate_password(password, email=email)
    if reasons:
        raise APIError(422, "That password is too weak", "weak_password", reasons=reasons)
    await _hit(limiter, SIGNUP_IP_LIMIT, request_ip(request))
    await _hit(limiter, SIGNUP_EMAIL_LIMIT, email)

    user = await service.user_by_email(session, email)
    password_hash = await hash_password(password)  # every branch, so timing matches
    emails = PendingEmails()
    body = {"status": "verification_sent", "email": email}

    if _is_superadmin_address(email, settings) or (
        user is not None and user.email_verified_at is not None
    ):
        emails.add(
            "account_exists",
            email,
            {
                "name": user.name if user is not None else name.strip(),
                "login_url": _link(settings, "/login"),
                "reset_url": _link(settings, "/forgot"),
            },
        )
        _send(emails)
        return body

    if user is None:
        user = User(email=email, name=name.strip(), password_hash=password_hash)
        session.add(user)
        await session.flush()
    else:  # never verified: the latest attempt wins
        user.name = name.strip()
        user.password_hash = password_hash
    code = await issue_code(
        session,
        settings=settings,
        email=email,
        purpose="verify",
        user_id=user.id,
        payload={
            "business_name": business_name.strip(),
            "signup_mode": engine.signup_mode,
            **({"legal": accepted_legal} if accepted_legal else {}),  # P3 legal
            **({"plan_id": str(chosen.id)} if chosen is not None else {}),  # P6 plan choice
        },
    )
    await session.commit()
    emails.add("verify_code", email, {"name": user.name, "code": code, "minutes": CODE_MINUTES})
    _send(emails)
    return body


async def verify(
    session: "AsyncSession",
    request: "Request",
    limiter: AccountRateLimiter,
    *,
    email: str,
    code: str,
) -> dict[str, Any]:
    """Consume the verify code, create the tenant with the user as owner, sign in.

    Returns the `TokenResponse` body; `tenant.status` is `active` or `pending`.
    """
    settings: Settings = request.app.state.settings
    email = normalize_email(email)
    await _hit(limiter, VERIFY_EMAIL_LIMIT, email)
    await _hit(limiter, VERIFY_IP_LIMIT, request_ip(request))
    row = await consume_code(
        session, settings=settings, email=email, purposes=("verify",), code=code
    )
    user = await service.user_by_email(session, email)
    if (
        user is None
        or user.email_verified_at is not None
        or _is_superadmin_address(email, settings)
    ):
        await session.commit()  # the code is spent either way
        raise APIError(
            400, "This code has expired or was already used. Ask for a new one.", "code_expired"
        )
    payload = row.payload or {}
    mode = "open" if payload.get("signup_mode") == "open" else "approval"
    business_name = str(payload.get("business_name") or user.name)[:255]

    now = datetime.now(UTC)
    user.email_verified_at = now
    user.last_login_at = now
    tenant, emails = await create_signup_tenant(
        session, user=user, business_name=business_name, signup_mode=mode, request=request
    )
    # P3 legal: what they accepted at sign-up, now that the person and the workspace exist
    legal_payload = payload.get("legal")
    if isinstance(legal_payload, dict) and legal_payload:
        from callobuzz_rag.legal import service as legal_service

        await legal_service.record_acceptances(
            session, legal_payload, user_id=user.id, tenant_id=tenant.id, email=email
        )
    # /P3 legal
    choice = await _apply_plan_choice(session, request, tenant, payload.get("plan_id"))
    session_row, raw = await create_session(session, user, tenant.id, request)
    principal = await service.principal_payload(session, user, tenant.id, settings)
    body = service.token_response(settings, session_row, raw, principal)
    if choice is not None:  # P6 plan choice
        body["plan_choice"] = choice
    await session.commit()
    emails.send()  # the operator's notice: best-effort, the account exists now
    return body


# P6 plan choice
async def _apply_plan_choice(
    session: "AsyncSession", request: "Request", tenant: Any, raw_plan_id: Any
) -> dict[str, Any] | None:
    """The plan chosen at sign-up: applied now in open mode (a plan with a fee waits for the
    owner's payment, nothing is charged), remembered for the approval in approval mode. A choice
    that no longer stands leaves the default plan."""
    from callobuzz_rag.billing.plan_change import apply_signup_choice, resolve_ref

    if not raw_plan_id:
        return None
    plan = await resolve_ref(session, str(raw_plan_id))
    if plan is None:
        return None
    if tenant.status != "active":
        tenant.requested_plan_id = plan.id
        return {"status": "on_approval", "plan_id": plan.id, "plan_name": plan.name}
    try:
        async with session.begin_nested():
            choice = await apply_signup_choice(session, tenant, plan.id, request=request)
    except APIError:  # never fail a sign-up over the plan: the default plan stands
        logger.warning("Plan chosen at sign-up could not be applied", exc_info=True)
        return None
    if choice is None:
        return None
    return {
        "status": choice.status,
        "plan_id": choice.plan.id,
        "plan_name": choice.plan.name,
        "amount_due_minor": choice.amount_due_minor,
    }


# /P6 plan choice


async def _code_mail_limits(limiter: AccountRateLimiter, request: "Request", email: str) -> None:
    await _hit(limiter, CODE_MAIL_IP_LIMIT, request_ip(request))
    await _hit(limiter, CODE_MAIL_EMAIL_MINUTE, email)
    await _hit(limiter, CODE_MAIL_EMAIL_HOUR, email)


async def resend(
    session: "AsyncSession",
    request: "Request",
    limiter: AccountRateLimiter,
    *,
    email: str,
    purpose: str,
) -> dict[str, str]:
    """Send a fresh code if one applies. Always `{"status": "sent_if_applicable"}`."""
    settings: Settings = request.app.state.settings
    if purpose == "reset":
        return await forgot(session, request, limiter, email=email)
    email = normalize_email(email)
    if not email_configured(settings):
        raise _email_not_configured()
    await _code_mail_limits(limiter, request, email)

    user = await service.user_by_email(session, email)
    if (
        user is None
        or user.email_verified_at is not None
        or _is_superadmin_address(email, settings)
    ):
        return dict(SENT_IF_APPLICABLE)
    last = (
        await session.execute(
            select(EmailCode)
            .where(EmailCode.email == email, EmailCode.purpose == "verify")
            .order_by(EmailCode.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last is None:  # nothing to resend: the sign-up must be started again
        return dict(SENT_IF_APPLICABLE)
    code = await issue_code(
        session,
        settings=settings,
        email=email,
        purpose="verify",
        user_id=user.id,
        payload=dict(last.payload or {}),
    )
    await session.commit()
    emails = PendingEmails()
    emails.add("verify_code", email, {"name": user.name, "code": code, "minutes": CODE_MINUTES})
    _send(emails)
    return dict(SENT_IF_APPLICABLE)


async def forgot(
    session: "AsyncSession",
    request: "Request",
    limiter: AccountRateLimiter,
    *,
    email: str,
) -> dict[str, str]:
    """E-mail a `reset` code to an active, verified account (or the super admin).
    Always `{"status": "sent_if_applicable"}`; an unverified account gets nothing (a
    reset must not verify an address whose workspace was never created)."""
    settings: Settings = request.app.state.settings
    email = normalize_email(email)
    if not email_configured(settings):
        raise _email_not_configured()
    await _code_mail_limits(limiter, request, email)

    user = await service.user_by_email(session, email)
    if user is None or not user.is_active:
        return dict(SENT_IF_APPLICABLE)
    if user.email_verified_at is None and not service.is_superadmin(user, settings):
        return dict(SENT_IF_APPLICABLE)
    code = await issue_code(
        session, settings=settings, email=email, purpose="reset", user_id=user.id
    )
    await session.commit()
    emails = PendingEmails()
    emails.add("reset_code", email, {"name": user.name, "code": code, "minutes": CODE_MINUTES})
    _send(emails)
    return dict(SENT_IF_APPLICABLE)
