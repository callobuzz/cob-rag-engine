"""Queueing and sending e-mail.

`enqueue_email` is what request handlers call: it checks the template and context at
once (so a bad call fails in the request, not later in the worker), then queues the
Celery task `callobuzz_rag.send_email` on the `email` queue, served by the `mailer`
service. The payload is `{template, to, context}`; the worker renders it at send time,
so the branding is current when the mail goes out. `expires` drops a message that
could only arrive after the code in it has expired.

`send_now` renders and sends in the caller's process: the SMTP test endpoint and the
super-admin bootstrap CLI use it.
"""

import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Final

from callobuzz_rag.accounts.email.factory import email_provider_from_settings
from callobuzz_rag.accounts.email.render import render_email, validate_context
from callobuzz_rag.accounts.settings_store import get_engine_settings
from callobuzz_rag.core.exceptions import (
    EmailNotConfiguredError,
    EmailQueueError,
    EmailTransientError,
)

if TYPE_CHECKING:
    from callobuzz_rag.accounts.email.base import EmailProvider
    from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

EMAIL_QUEUE: Final = "email"
SEND_EMAIL_TASK: Final = "callobuzz_rag.send_email"

# Message lifetimes (`apply_async(expires=...)`): a message still queued after this is
# dropped, because what it carries has expired too.
CODE_EXPIRES_S: Final = 600  # verification / reset codes (10 minutes)
SETUP_EXPIRES_S: Final = 24 * 3600  # super-admin set-password code
INVITATION_EXPIRES_S: Final = 7 * 24 * 3600
NOTICE_EXPIRES_S: Final = 24 * 3600  # sign-up notice, activated, suspended, ...

# Publishing must fail fast when Redis is down: the request answers 503, not a hang.
_PUBLISH_RETRY_POLICY: Final = {
    "max_retries": 2,
    "interval_start": 0,
    "interval_step": 0.5,
    "interval_max": 1,
}


def enqueue_email(
    template: str, to: str, context: Mapping[str, Any], *, expires_s: int | float
) -> None:
    """Queue `template` for `to`.

    Raises:
        EmailTemplateError: unknown template, bad recipient, missing/invalid context.
        EmailQueueError: the broker could not take the message (routes answer 503
            `email_unavailable`).
    """
    validate_context(template, to, context)
    # L5a demo: nothing leaves a demo server
    from callobuzz_rag.demo import mode as demo_mode

    if demo_mode.is_demo():
        logger.info("demo: e-mail %s not sent", template)
        return
    # /L5a demo
    from callobuzz_rag.worker.email_tasks import send_email_task

    try:
        send_email_task.apply_async(
            kwargs={"template": template, "to": to, "context": dict(context)},
            queue=EMAIL_QUEUE,
            expires=expires_s,
            retry=True,
            retry_policy=_PUBLISH_RETRY_POLICY,
        )
    except Exception as exc:
        logger.error("Could not queue e-mail %s: %s", template, type(exc).__name__)
        raise EmailQueueError("could not queue the e-mail") from exc


async def send_now(
    template: str,
    to: str,
    context: Mapping[str, Any],
    settings: "Settings",
    session_factory: Callable[[], Any],
    *,
    provider: "EmailProvider | None" = None,
) -> str:
    """Render with the current branding and send in this process; returns the
    provider's name.

    Raises:
        EmailNotConfiguredError: no provider configured.
        EmailTemplateError: bad template call.
        EmailTransientError / EmailPermanentError: the send (or loading the branding)
            failed.
    """
    provider = provider or email_provider_from_settings(settings)
    if provider.name == "disabled":
        raise EmailNotConfiguredError("E-mail is not configured (set RAG_SMTP_HOST)")
    validate_context(template, to, context)
    try:
        branding = (await get_engine_settings(session_factory)).branding
    except Exception as exc:
        raise EmailTransientError("could not load the branding", error=type(exc).__name__) from exc
    email = render_email(template, to, context, branding, settings.dashboard_url)
    await provider.send(email)
    return provider.name
