"""Celery task that sends transactional e-mail (accounts A2).

Routed to the `email` queue, served by the `mailer` compose service (threads pool) so
a 10-minute code never waits behind a long ingest. The payload is `{template, to,
context}`; rendering happens here, with the branding current at send time.

Transient failures (connection, timeout, disconnect, 4xx) are retried after 15, 30,
60, 120 and 240 seconds (about 7.5 minutes in all, inside a code's 10-minute life),
then dropped with an error log. Permanent failures are logged once, with the SMTP code
and never the body. `ignore_result=True`: the result backend never holds a context
that may carry a code.

The provider is built once per process at import, so a refused configuration (the
log provider in production, SMTP without a host or sender) stops the worker booting.
"""

import asyncio
import logging
from collections.abc import Mapping
from typing import Any, Final

from callobuzz_rag.accounts.email.dispatch import SEND_EMAIL_TASK, send_now
from callobuzz_rag.accounts.email.factory import email_provider_from_settings
from callobuzz_rag.core.config import get_settings
from callobuzz_rag.core.exceptions import EmailError, EmailTransientError
from callobuzz_rag.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

BACKOFF_S: Final = (15, 30, 60, 120, 240)

_settings = get_settings()
_provider = email_provider_from_settings(_settings)


async def _send(template: str, to: str, context: Mapping[str, Any]) -> None:
    from callobuzz_rag.db.engine import create_engine, create_session_factory

    engine = create_engine(_settings)
    try:
        await send_now(
            template,
            to,
            context,
            _settings,
            create_session_factory(engine),
            provider=_provider,
        )
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name=SEND_EMAIL_TASK,
    max_retries=len(BACKOFF_S),
    ignore_result=True,
)
def send_email_task(self: Any, template: str, to: str, context: dict[str, Any]) -> None:
    try:
        asyncio.run(_send(template, to, context))
    except EmailTransientError as exc:
        attempt = self.request.retries
        if attempt < len(BACKOFF_S):
            logger.warning(
                "E-mail %s to %s deferred (%s, SMTP code %s); retry %d/%d in %ds",
                template,
                to,
                exc.error,
                exc.smtp_code,
                attempt + 1,
                len(BACKOFF_S),
                BACKOFF_S[attempt],
            )
            raise self.retry(countdown=BACKOFF_S[attempt]) from None
        logger.error(
            "E-mail %s to %s dropped after %d retries (%s, SMTP code %s)",
            template,
            to,
            attempt,
            exc.error,
            exc.smtp_code,
        )
    except EmailError as exc:
        logger.error(
            "E-mail %s to %s failed permanently (%s, SMTP code %s)",
            template,
            to,
            getattr(exc, "error", type(exc).__name__),
            getattr(exc, "smtp_code", None),
        )
    else:
        logger.info("E-mail %s sent to %s via %s", template, to, _provider.name)
