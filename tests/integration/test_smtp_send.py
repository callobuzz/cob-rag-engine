"""A real SMTP conversation with a local aiosmtpd server (accounts A2).

No Docker needed, but marked integration: it opens real sockets.
"""

import email
import email.policy
import socket
from collections.abc import Iterator
from email.message import EmailMessage
from typing import Any

import pytest
from aiosmtpd.controller import Controller

from callobuzz_rag.accounts.email.render import render_email
from callobuzz_rag.accounts.email.smtp import SMTPEmailProvider
from callobuzz_rag.accounts.settings_store import POWERED_BY, Branding
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import EmailPermanentError, EmailTransientError

pytestmark = pytest.mark.integration

FROM = "Sahayak AI <no-reply@sahayak.example>"
TO = "owner@shree.example"


class Inbox:
    """aiosmtpd handler: keeps every message; refuses recipients at refused.example."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, list[str], EmailMessage]] = []

    async def handle_RCPT(  # noqa: N802 - aiosmtpd hook name
        self, server: Any, session: Any, envelope: Any, address: str, rcpt_options: Any
    ) -> str:
        if address.endswith("@refused.example"):
            return "550 5.1.1 No such user here"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(  # noqa: N802 - aiosmtpd hook name
        self, server: Any, session: Any, envelope: Any
    ) -> str:
        parsed = email.message_from_bytes(envelope.content, policy=email.policy.default)
        assert isinstance(parsed, EmailMessage)
        self.messages.append((envelope.mail_from, list(envelope.rcpt_tos), parsed))
        return "250 Message accepted for delivery"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture
def smtp_server() -> Iterator[tuple[Inbox, int]]:
    inbox = Inbox()
    port = _free_port()
    controller = Controller(inbox, hostname="127.0.0.1", port=port)
    controller.start()
    try:
        yield inbox, port
    finally:
        controller.stop()


def _provider(port: int) -> SMTPEmailProvider:
    settings = Settings(
        _env_file=None,
        smtp_host="127.0.0.1",
        smtp_port=port,
        smtp_security="none",
        smtp_from=FROM,
        smtp_timeout_seconds=5,
    )
    return SMTPEmailProvider(settings)


async def test_a_real_send_arrives_with_both_parts_and_headers(
    smtp_server: tuple[Inbox, int],
) -> None:
    inbox, port = smtp_server
    branding = Branding(product_name="Sahayak AI", primary_color="#0a7d5a")
    outgoing = render_email(
        "verify_code",
        TO,
        {"name": "Asha", "code": "482913", "minutes": 10},
        branding,
        "https://app.sahayak.example",
    )
    await _provider(port).send(outgoing)

    ((mail_from, rcpt_tos, message),) = inbox.messages
    assert mail_from == "no-reply@sahayak.example"
    assert rcpt_tos == [TO]
    assert message["From"] == FROM
    assert message["To"] == TO
    assert message["Subject"] == "Your Sahayak AI verification code: 482913"
    assert message["Date"]
    assert message["Message-ID"].endswith("@sahayak.example>")
    assert message["Auto-Submitted"] == "auto-generated"
    assert message.get_content_type() == "multipart/alternative"
    text = message.get_body(("plain",))
    html = message.get_body(("html",))
    assert text is not None and html is not None
    assert "482913" in text.get_content()
    assert text.get_content().rstrip().endswith(POWERED_BY["text"])
    assert "#0a7d5a" in html.get_content()
    assert POWERED_BY["text"] in html.get_content()
    assert f">{POWERED_BY['text']}</a>" not in html.get_content()


async def test_non_ascii_names_survive(smtp_server: tuple[Inbox, int]) -> None:
    inbox, port = smtp_server
    outgoing = render_email(
        "suspended", TO, {"tenant_name": "श्री लैब्स"}, Branding(), "http://127.0.0.1:8090"
    )
    await _provider(port).send(outgoing)
    ((_, _, message),) = inbox.messages
    assert message["Subject"] == "श्री लैब्स has been suspended"
    body = message.get_body(("plain",))
    assert body is not None and "श्री लैब्स" in body.get_content()


async def test_a_refused_recipient_is_permanent(smtp_server: tuple[Inbox, int]) -> None:
    inbox, port = smtp_server
    outgoing = render_email(
        "smtp_test", "nobody@refused.example", {"sent_at": "now"}, Branding(), "http://x.example"
    )
    with pytest.raises(EmailPermanentError) as caught:
        await _provider(port).send(outgoing)
    assert caught.value.smtp_code == 550
    assert caught.value.error == "SMTPRecipientsRefused"
    assert not inbox.messages


async def test_nothing_listening_is_transient() -> None:
    outgoing = render_email(
        "smtp_test", TO, {"sent_at": "now"}, Branding(), "http://127.0.0.1:8090"
    )
    with pytest.raises(EmailTransientError) as caught:
        await _provider(_free_port()).send(outgoing)
    assert caught.value.smtp_code is None


async def test_starttls_is_mandatory_when_configured(smtp_server: tuple[Inbox, int]) -> None:
    """The local server offers no STARTTLS: `starttls` must fail, never fall back to plain."""
    inbox, port = smtp_server
    settings = Settings(
        _env_file=None,
        smtp_host="127.0.0.1",
        smtp_port=port,
        smtp_security="starttls",
        smtp_from=FROM,
        smtp_timeout_seconds=5,
    )
    outgoing = render_email(
        "smtp_test", TO, {"sent_at": "now"}, Branding(), "http://127.0.0.1:8090"
    )
    with pytest.raises(EmailPermanentError) as caught:
        await SMTPEmailProvider(settings).send(outgoing)
    assert caught.value.error == "SMTPException"
    assert not inbox.messages
