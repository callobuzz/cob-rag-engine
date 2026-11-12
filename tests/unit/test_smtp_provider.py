"""SMTPEmailProvider: security flags, message format, error mapping (accounts A2)."""

from email.message import EmailMessage
from typing import Any
from unittest.mock import AsyncMock, patch

import aiosmtplib
import pytest

from callobuzz_rag.accounts.email.base import OutgoingEmail
from callobuzz_rag.accounts.email.smtp import SMTPEmailProvider, classify_smtp_error
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import EmailPermanentError, EmailTransientError

FROM = "Sahayak AI <no-reply@sahayak.example>"
MAIL = OutgoingEmail(
    to="owner@shree.example", subject="Your code", text="Code: 123456", html="<p>123456</p>"
)


def _provider(**kwargs: Any) -> SMTPEmailProvider:
    values: dict[str, Any] = {"smtp_host": "smtp.example.com", "smtp_from": FROM, **kwargs}
    return SMTPEmailProvider(Settings(_env_file=None, **values))


async def _send(provider: SMTPEmailProvider) -> tuple[EmailMessage, dict[str, Any]]:
    with patch("aiosmtplib.send", new=AsyncMock(return_value=({}, "OK"))) as send:
        await provider.send(MAIL)
    send.assert_awaited_once()
    return send.await_args.args[0], send.await_args.kwargs


@pytest.mark.parametrize(
    ("security", "use_tls", "start_tls"),
    [("starttls", False, True), ("ssl", True, False), ("none", False, False)],
)
async def test_security_modes_map_to_explicit_flags(
    security: str, use_tls: bool, start_tls: bool
) -> None:
    _, kwargs = await _send(_provider(smtp_security=security, smtp_port=2525))
    assert kwargs["use_tls"] is use_tls
    assert kwargs["start_tls"] is start_tls
    assert kwargs["hostname"] == "smtp.example.com"
    assert kwargs["port"] == 2525
    assert kwargs["timeout"] == 20.0


async def test_no_auth_without_a_username() -> None:
    _, kwargs = await _send(_provider(smtp_password="unused"))
    assert "username" not in kwargs and "password" not in kwargs


async def test_auth_with_username_and_password() -> None:
    _, kwargs = await _send(_provider(smtp_username="mailer", smtp_password="s3cret"))
    assert kwargs["username"] == "mailer"
    assert kwargs["password"] == "s3cret"


async def test_message_has_both_parts_and_the_headers() -> None:
    message, _ = await _send(_provider())
    assert message["From"] == FROM
    assert message["To"] == MAIL.to
    assert message["Subject"] == MAIL.subject
    assert message["Date"]
    assert message["Message-ID"].endswith("@sahayak.example>")
    assert message["Auto-Submitted"] == "auto-generated"
    assert message.get_content_type() == "multipart/alternative"
    parts = [(p.get_content_type(), p.get_content().strip()) for p in message.iter_parts()]
    assert parts == [("text/plain", "Code: 123456"), ("text/html", "<p>123456</p>")]


@pytest.mark.parametrize(
    ("exc", "kind", "code"),
    [
        (aiosmtplib.SMTPResponseException(421, "busy"), EmailTransientError, 421),
        (aiosmtplib.SMTPResponseException(451, "try later"), EmailTransientError, 451),
        (aiosmtplib.SMTPDataError(452, "full"), EmailTransientError, 452),
        (aiosmtplib.SMTPAuthenticationError(535, "bad creds"), EmailPermanentError, 535),
        (aiosmtplib.SMTPResponseException(550, "no such user"), EmailPermanentError, 550),
        (aiosmtplib.SMTPSenderRefused(553, "sender", "x@y"), EmailPermanentError, 553),
        (
            aiosmtplib.SMTPRecipientsRefused(
                [aiosmtplib.SMTPRecipientRefused(550, "nope", "a@b.example")]
            ),
            EmailPermanentError,
            550,
        ),
        (aiosmtplib.SMTPConnectError("refused"), EmailTransientError, None),
        (aiosmtplib.SMTPConnectTimeoutError("slow"), EmailTransientError, None),
        (aiosmtplib.SMTPTimeoutError("slow"), EmailTransientError, None),
        (aiosmtplib.SMTPServerDisconnected("gone"), EmailTransientError, None),
        (ConnectionResetError("reset"), EmailTransientError, None),
        (aiosmtplib.SMTPNotSupported("no STARTTLS"), EmailPermanentError, None),
    ],
)
async def test_exception_mapping(exc: BaseException, kind: type, code: int | None) -> None:
    with (
        patch("aiosmtplib.send", new=AsyncMock(side_effect=exc)),
        pytest.raises(kind) as caught,
    ):
        await _provider().send(MAIL)
    assert type(caught.value) is kind
    assert caught.value.smtp_code == code
    assert caught.value.error == type(exc).__name__
    assert caught.value.__cause__ is None  # the server's text is not kept


def test_server_text_never_reaches_the_error() -> None:
    err = classify_smtp_error(
        aiosmtplib.SMTPAuthenticationError(535, "5.7.8 user mailer@internal.host rejected")
    )
    assert "internal.host" not in str(err)
    assert "mailer" not in str(err)
