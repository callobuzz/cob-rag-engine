"""SMTP provider (aiosmtplib).

`RAG_SMTP_SECURITY`: `starttls` makes STARTTLS mandatory (the send fails if the server
does not offer it), `ssl` is implicit TLS (usually port 465), `none` is plain text for a
local relay. Failures are sorted into transient (retried by the task) and permanent;
only the exception class name and the SMTP code are kept, never the server's text.
"""

from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from typing import TYPE_CHECKING, Any

from callobuzz_rag.accounts.email.base import EmailProvider, OutgoingEmail
from callobuzz_rag.core.exceptions import EmailPermanentError, EmailTransientError

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings


def from_domain(from_header: str) -> str:
    """The domain of `RAG_SMTP_FROM`'s address ("" when it has none)."""
    address = parseaddr(from_header)[1]
    return address.rpartition("@")[2] if "@" in address else ""


def security_flags(security: str) -> dict[str, bool]:
    """aiosmtplib's `use_tls` / `start_tls` for a `RAG_SMTP_SECURITY` value."""
    if security == "ssl":
        return {"use_tls": True, "start_tls": False}
    if security == "none":
        return {"use_tls": False, "start_tls": False}
    return {"use_tls": False, "start_tls": True}


def build_message(email: OutgoingEmail, from_header: str) -> EmailMessage:
    """Plain text first, HTML as the alternative, with the headers every mail carries."""
    message = EmailMessage()
    message["From"] = from_header
    message["To"] = email.to
    message["Subject"] = email.subject
    message["Date"] = formatdate(usegmt=True)
    message["Message-ID"] = make_msgid(domain=from_domain(from_header) or None)
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(email.text)
    message.add_alternative(email.html, subtype="html")
    return message


def classify_smtp_error(exc: BaseException) -> EmailTransientError | EmailPermanentError:
    """Map an aiosmtplib (or socket) failure to a transient or permanent error."""
    import aiosmtplib

    name = type(exc).__name__
    if isinstance(exc, aiosmtplib.SMTPRecipientsRefused):
        code = exc.recipients[0].code if exc.recipients else None
        return EmailPermanentError("recipient refused", smtp_code=code, error=name)
    if isinstance(exc, aiosmtplib.SMTPAuthenticationError | aiosmtplib.SMTPSenderRefused):
        return EmailPermanentError("SMTP server refused", smtp_code=exc.code, error=name)
    if isinstance(exc, aiosmtplib.SMTPResponseException):
        if 400 <= exc.code < 500:
            return EmailTransientError("SMTP server deferred", smtp_code=exc.code, error=name)
        return EmailPermanentError("SMTP server refused", smtp_code=exc.code, error=name)
    if isinstance(
        exc,
        aiosmtplib.SMTPConnectError
        | aiosmtplib.SMTPTimeoutError
        | aiosmtplib.SMTPServerDisconnected
        | OSError,
    ):
        return EmailTransientError("SMTP server unreachable", error=name)
    return EmailPermanentError("SMTP send failed", error=name)


class SMTPEmailProvider(EmailProvider):
    name = "smtp"

    def __init__(self, settings: "Settings") -> None:
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._username = settings.smtp_username or None
        self._password = settings.smtp_password.get_secret_value() or None
        self._from = settings.smtp_from
        self._timeout = settings.smtp_timeout_seconds
        self._flags = security_flags(settings.smtp_security)

    def __repr__(self) -> str:  # never the password
        return f"SMTPEmailProvider(host={self._host!r}, port={self._port})"

    async def send(self, email: OutgoingEmail) -> None:
        import aiosmtplib

        message = build_message(email, self._from)
        kwargs: dict[str, Any] = {
            "hostname": self._host,
            "port": self._port,
            "timeout": self._timeout,
            **self._flags,
        }
        if self._username:
            kwargs["username"] = self._username
            kwargs["password"] = self._password or ""
        try:
            await aiosmtplib.send(message, **kwargs)
        except Exception as exc:
            raise classify_smtp_error(exc) from None
