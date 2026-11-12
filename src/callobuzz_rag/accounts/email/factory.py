"""Choosing the e-mail provider from the settings.

`RAG_EMAIL_PROVIDER=auto` (default): smtp when `RAG_SMTP_HOST` is set, else the log
provider outside production, else disabled. `email_provider_from_settings` runs at API
startup and at worker import, so a refused combination stops the process from booting.
"""

from typing import TYPE_CHECKING, Literal

from callobuzz_rag.accounts.email.base import EmailProvider

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings

ProviderName = Literal["smtp", "log", "disabled"]


def resolve_provider_name(settings: "Settings") -> ProviderName:
    # L5a demo: a demo server sends no e-mail, whatever SMTP says
    if getattr(settings, "demo_mode", False) is True:
        return "disabled"
    # /L5a demo
    choice = settings.email_provider
    if choice != "auto":
        return choice
    if settings.smtp_host:
        return "smtp"
    return "disabled" if settings.is_prod else "log"


def email_configured(settings: "Settings") -> bool:
    """True when mail can actually go out (or, in development, to the log)."""
    return resolve_provider_name(settings) != "disabled"


def email_provider_from_settings(settings: "Settings") -> EmailProvider:
    """The configured provider.

    Raises:
        RuntimeError: the log provider in production (it would write codes to the
            log), or smtp without `RAG_SMTP_HOST` / `RAG_SMTP_FROM`.
    """
    name = resolve_provider_name(settings)
    if name == "log":
        if settings.is_prod:
            raise RuntimeError(
                "RAG_EMAIL_PROVIDER=log is refused when RAG_ENV=prod: it writes every e-mail, "
                "verification and reset codes included, to the log. Configure SMTP "
                "(RAG_SMTP_HOST, RAG_SMTP_FROM, ...) or set RAG_EMAIL_PROVIDER=disabled."
            )
        from callobuzz_rag.accounts.email.log import LogEmailProvider

        return LogEmailProvider()
    if name == "smtp":
        missing = [
            var
            for var, value in (
                ("RAG_SMTP_HOST", settings.smtp_host),
                ("RAG_SMTP_FROM", settings.smtp_from),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                f"The SMTP e-mail provider needs {' and '.join(missing)} "
                '(e.g. RAG_SMTP_FROM="Your Product <no-reply@example.com>").'
            )
        from callobuzz_rag.accounts.email.smtp import SMTPEmailProvider, from_domain

        if not from_domain(settings.smtp_from) or any(c in settings.smtp_from for c in "\r\n"):
            raise RuntimeError(
                "RAG_SMTP_FROM must be an e-mail address, optionally with a name: "
                '"Your Product <no-reply@example.com>".'
            )
        return SMTPEmailProvider(settings)
    from callobuzz_rag.accounts.email.disabled import DisabledEmailProvider

    return DisabledEmailProvider()
