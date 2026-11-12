"""E-mail configuration status and SMTP test (admin only, accounts A2).

- `GET /api/v1/admin/smtp`: which provider is in use and how SMTP is configured. Never
  the username or password, only whether they are set.
- `POST /api/v1/admin/smtp/test`: sends the `smtp_test` template synchronously (not
  through Celery), so the admin sees the actual result or error. A failure reports the
  SMTP code and the exception class name only; server replies can carry hostnames or
  usernames.
"""

import asyncio
from datetime import UTC, datetime
from typing import Final, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from callobuzz_rag.accounts.email.dispatch import send_now
from callobuzz_rag.accounts.email.factory import (
    email_configured,
    email_provider_from_settings,
    resolve_provider_name,
)
from callobuzz_rag.accounts.email_rules import ValidatedEmail
from callobuzz_rag.api.deps import require_admin
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.core.exceptions import (
    EmailNotConfiguredError,
    EmailPermanentError,
    EmailTransientError,
)

router = APIRouter(
    prefix="/api/v1/admin/smtp", tags=["email"], dependencies=[Depends(require_admin)]
)

TEST_TIMEOUT_S: Final = 20.0
NOT_CONFIGURED_DETAIL: Final = "E-mail is not configured (set RAG_SMTP_HOST)"


class SMTPStatusResponse(BaseModel):
    provider: Literal["smtp", "log", "disabled"]
    configured: bool
    host: str | None
    port: int
    security: Literal["starttls", "ssl", "none"]
    from_address: str | None
    username_set: bool
    password_set: bool
    dashboard_url: str


class SMTPTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: ValidatedEmail


class SMTPTestResponse(BaseModel):
    sent: bool
    provider: Literal["smtp", "log"]
    to: str


@router.get("", response_model=SMTPStatusResponse)
async def smtp_status(request: Request) -> SMTPStatusResponse:
    """The e-mail provider in use and the SMTP settings, without secret values."""
    settings = request.app.state.settings
    return SMTPStatusResponse(
        provider=resolve_provider_name(settings),
        configured=email_configured(settings),
        host=settings.smtp_host or None,
        port=settings.smtp_port,
        security=settings.smtp_security,
        from_address=settings.smtp_from or None,
        username_set=bool(settings.smtp_username),
        password_set=bool(settings.smtp_password.get_secret_value()),
        dashboard_url=settings.dashboard_url,
    )


@router.post(
    "/test",
    response_model=SMTPTestResponse,
    responses={
        502: {"description": "The SMTP send failed (`smtp_test_failed`)"},
        503: {"description": "No e-mail provider configured (`email_not_configured`)"},
    },
)
async def smtp_test(body: SMTPTestRequest, request: Request) -> SMTPTestResponse:
    """Send a test e-mail now, with the current branding, and report the result."""
    settings = request.app.state.settings
    if not email_configured(settings):
        raise APIError(503, NOT_CONFIGURED_DETAIL, "email_not_configured")
    provider = email_provider_from_settings(settings)
    to = str(body.to)
    try:
        async with asyncio.timeout(TEST_TIMEOUT_S):
            name = await send_now(
                "smtp_test",
                to,
                {"sent_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")},
                settings,
                request.app.state.session_factory,
                provider=provider,
            )
    except EmailNotConfiguredError as exc:
        raise APIError(503, NOT_CONFIGURED_DETAIL, "email_not_configured") from exc
    except (EmailTransientError, EmailPermanentError) as exc:
        detail = (
            "The SMTP server refused the message"
            if exc.smtp_code is not None
            else "The test e-mail could not be sent"
        )
        raise APIError(
            502, detail, "smtp_test_failed", smtp_code=exc.smtp_code, error=exc.error
        ) from exc
    except TimeoutError as exc:
        raise APIError(
            502,
            "The test e-mail could not be sent",
            "smtp_test_failed",
            smtp_code=None,
            error="TimeoutError",
        ) from exc
    return SMTPTestResponse(sent=True, provider="log" if name == "log" else "smtp", to=to)
