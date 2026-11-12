"""The send_email Celery task and enqueue_email (accounts A2)."""

import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from celery.exceptions import Retry

from callobuzz_rag.accounts.email import dispatch
from callobuzz_rag.accounts.email.base import EmailProvider, OutgoingEmail
from callobuzz_rag.accounts.email.dispatch import CODE_EXPIRES_S, enqueue_email, send_now
from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import (
    EmailNotConfiguredError,
    EmailPermanentError,
    EmailQueueError,
    EmailTemplateError,
    EmailTransientError,
)
from callobuzz_rag.worker import email_tasks
from callobuzz_rag.worker.email_tasks import BACKOFF_S, send_email_task

TO = "owner@shree.example"
CONTEXT = {"name": "Asha", "code": "482913", "minutes": 10}


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    invalidate_engine_settings_cache()
    yield
    invalidate_engine_settings_cache()


def _run(side_effect: Any, retries: int = 0) -> tuple[MagicMock, bool]:
    """Run the task body once as retry number `retries`.

    Returns the `retry` mock and whether the task raised `Retry`.
    """
    retry = MagicMock(side_effect=lambda **kw: Retry("retry", when=kw.get("countdown")))
    send_email_task.push_request(retries=retries)
    try:
        with (
            patch.object(email_tasks, "_send", new=AsyncMock(side_effect=side_effect)),
            patch.object(send_email_task, "retry", new=retry),
        ):
            send_email_task.run("verify_code", TO, CONTEXT)
    except Retry:
        return retry, True
    finally:
        send_email_task.pop_request()
    return retry, False


def test_task_registration() -> None:
    assert send_email_task.name == "callobuzz_rag.send_email"
    assert send_email_task.ignore_result is True
    assert send_email_task.max_retries == 5
    assert BACKOFF_S == (15, 30, 60, 120, 240)
    routes = send_email_task.app.conf.task_routes
    assert routes["callobuzz_rag.send_email"] == {"queue": "email"}


def test_success_does_not_retry(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=email_tasks.__name__)
    retry, raised = _run(None)
    assert not raised
    retry.assert_not_called()
    assert "482913" not in caplog.text


@pytest.mark.parametrize("attempt", range(5))
def test_transient_failure_retries_with_the_countdown_table(attempt: int) -> None:
    retry, raised = _run(EmailTransientError("busy", smtp_code=421, error="X"), attempt)
    assert raised
    retry.assert_called_once_with(countdown=BACKOFF_S[attempt])


def test_transient_failure_after_the_last_retry_is_dropped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger=email_tasks.__name__)
    retry, raised = _run(
        EmailTransientError("busy", smtp_code=421, error="SMTPResponseException"), 5
    )
    assert not raised
    retry.assert_not_called()
    assert "dropped after 5 retries" in caplog.text
    assert "421" in caplog.text


@pytest.mark.parametrize(
    "exc",
    [
        EmailPermanentError("refused", smtp_code=550, error="SMTPRecipientsRefused"),
        EmailPermanentError("auth", smtp_code=535, error="SMTPAuthenticationError"),
        EmailTemplateError("template 'x' is missing code"),
        EmailNotConfiguredError("off"),
    ],
)
def test_permanent_failure_is_logged_and_not_retried(
    exc: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=email_tasks.__name__)
    retry, raised = _run(exc)
    assert not raised
    retry.assert_not_called()
    assert "failed permanently" in caplog.text
    assert "482913" not in caplog.text  # never the body or the context


async def test_worker_send_renders_with_branding_and_uses_the_provider() -> None:
    sent: list[OutgoingEmail] = []

    class Capture(EmailProvider):
        name = "capture"

        async def send(self, email: OutgoingEmail) -> None:
            sent.append(email)

    row = MagicMock(value={"branding": {"product_name": "Sahayak AI"}})
    session = AsyncMock()
    session.get = AsyncMock(return_value=row)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    name = await send_now(
        "verify_code", TO, CONTEXT, Settings(_env_file=None), factory, provider=Capture()
    )
    assert name == "capture"
    (email,) = sent
    assert email.subject == "Your Sahayak AI verification code: 482913"


async def test_branding_that_cannot_load_is_transient() -> None:
    factory = MagicMock(side_effect=ConnectionRefusedError("db down"))
    provider = AsyncMock(spec=EmailProvider)
    provider.name = "smtp"
    with pytest.raises(EmailTransientError) as caught:
        await send_now(
            "verify_code", TO, CONTEXT, Settings(_env_file=None), factory, provider=provider
        )
    assert caught.value.error == "ConnectionRefusedError"
    provider.send.assert_not_awaited()


async def test_send_now_refuses_when_disabled() -> None:
    settings = Settings(_env_file=None, env="prod")
    with pytest.raises(EmailNotConfiguredError):
        await send_now("smtp_test", TO, {"sent_at": "now"}, settings, MagicMock())


# ----------------------------------------------------------------- enqueue


def test_enqueue_uses_the_email_queue_and_expiry() -> None:
    with patch.object(send_email_task, "apply_async") as apply_async:
        enqueue_email("verify_code", TO, CONTEXT, expires_s=CODE_EXPIRES_S)
    apply_async.assert_called_once()
    kwargs = apply_async.call_args.kwargs
    assert kwargs["queue"] == "email"
    assert kwargs["expires"] == 600
    assert kwargs["kwargs"] == {"template": "verify_code", "to": TO, "context": CONTEXT}


@pytest.mark.parametrize(
    ("template", "context", "match"),
    [
        ("welcome", {}, "unknown"),
        ("verify_code", {"name": "Asha", "minutes": 10}, "missing code"),
        ("verify_code", {**CONTEXT, "extra": object()}, "JSON"),
    ],
)
def test_enqueue_rejects_bad_calls_before_touching_celery(
    template: str, context: dict[str, Any], match: str
) -> None:
    with (
        patch.object(send_email_task, "apply_async") as apply_async,
        pytest.raises(EmailTemplateError, match=match),
    ):
        enqueue_email(template, TO, context, expires_s=600)
    apply_async.assert_not_called()


def test_enqueue_never_imports_the_task_for_a_bad_call() -> None:
    with (
        patch.object(dispatch, "validate_context", side_effect=EmailTemplateError("bad")),
        patch.object(send_email_task, "apply_async") as apply_async,
        pytest.raises(EmailTemplateError),
    ):
        enqueue_email("verify_code", TO, CONTEXT, expires_s=600)
    apply_async.assert_not_called()


def test_broker_failure_is_an_email_queue_error() -> None:
    from kombu.exceptions import OperationalError

    with (
        patch.object(send_email_task, "apply_async", side_effect=OperationalError("down")),
        pytest.raises(EmailQueueError),
    ):
        enqueue_email("verify_code", TO, CONTEXT, expires_s=600)
