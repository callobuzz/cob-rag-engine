"""Choosing the e-mail provider from the settings (accounts A2)."""

from typing import Any

import pytest
from pydantic import ValidationError

from callobuzz_rag.accounts.email.disabled import DisabledEmailProvider
from callobuzz_rag.accounts.email.factory import (
    email_configured,
    email_provider_from_settings,
    resolve_provider_name,
)
from callobuzz_rag.accounts.email.log import LogEmailProvider
from callobuzz_rag.accounts.email.smtp import SMTPEmailProvider
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import EmailNotConfiguredError

FROM = "Sahayak AI <no-reply@sahayak.example>"


def _settings(**kwargs: Any) -> Settings:
    return Settings(_env_file=None, **kwargs)


def test_auto_is_log_in_development() -> None:
    settings = _settings()
    assert settings.email_provider == "auto"
    assert resolve_provider_name(settings) == "log"
    assert isinstance(email_provider_from_settings(settings), LogEmailProvider)
    assert email_configured(settings)


@pytest.mark.parametrize("env", ["dev", "prod"])
def test_auto_is_smtp_when_a_host_is_set(env: str) -> None:
    settings = _settings(env=env, smtp_host="smtp.example.com", smtp_from=FROM)
    assert resolve_provider_name(settings) == "smtp"
    assert isinstance(email_provider_from_settings(settings), SMTPEmailProvider)
    assert email_configured(settings)


def test_auto_is_disabled_in_production_without_a_host() -> None:
    settings = _settings(env="prod")
    assert resolve_provider_name(settings) == "disabled"
    assert isinstance(email_provider_from_settings(settings), DisabledEmailProvider)
    assert not email_configured(settings)


async def test_disabled_provider_refuses_to_send() -> None:
    from callobuzz_rag.accounts.email.base import OutgoingEmail

    with pytest.raises(EmailNotConfiguredError):
        await DisabledEmailProvider().send(OutgoingEmail("a@b.example", "s", "t", "<p>h</p>"))


@pytest.mark.parametrize("env", ["prod", "production", " PROD "])
def test_explicit_log_in_production_refuses_to_start(env: str) -> None:
    settings = _settings(env=env, email_provider="log")
    with pytest.raises(RuntimeError, match="refused when RAG_ENV=prod"):
        email_provider_from_settings(settings)


def test_explicit_log_even_with_a_host_outside_production() -> None:
    settings = _settings(email_provider="log", smtp_host="smtp.example.com")
    assert isinstance(email_provider_from_settings(settings), LogEmailProvider)


def test_smtp_without_from_refuses_to_start() -> None:
    settings = _settings(smtp_host="smtp.example.com")
    with pytest.raises(RuntimeError, match="RAG_SMTP_FROM"):
        email_provider_from_settings(settings)


def test_explicit_smtp_without_host_refuses_to_start() -> None:
    settings = _settings(email_provider="smtp", smtp_from=FROM)
    with pytest.raises(RuntimeError, match="RAG_SMTP_HOST"):
        email_provider_from_settings(settings)


@pytest.mark.parametrize("sender", ["no address", "Name <>", "a@b.example\r\nBcc: x@y.example"])
def test_smtp_from_must_be_an_address(sender: str) -> None:
    settings = _settings(smtp_host="smtp.example.com", smtp_from=sender)
    with pytest.raises(RuntimeError, match="RAG_SMTP_FROM"):
        email_provider_from_settings(settings)


def test_explicit_disabled() -> None:
    settings = _settings(email_provider="disabled", smtp_host="smtp.example.com")
    assert resolve_provider_name(settings) == "disabled"
    assert not email_configured(settings)


def test_choices_are_validated_and_normalised() -> None:
    settings = _settings(email_provider=" SMTP ", smtp_security="SSL")
    assert settings.email_provider == "smtp"
    assert settings.smtp_security == "ssl"
    with pytest.raises(ValidationError):
        _settings(email_provider="sendgrid")
    with pytest.raises(ValidationError):
        _settings(smtp_security="tls")


def test_smtp_provider_repr_never_shows_the_password() -> None:
    settings = _settings(
        smtp_host="smtp.example.com", smtp_from=FROM, smtp_password="hunter2-secret"
    )
    provider = email_provider_from_settings(settings)
    assert "hunter2" not in repr(provider)
    assert "hunter2" not in repr(settings)


# --------------------------------------------------- refused at startup


@pytest.fixture
def _log_in_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    monkeypatch.setenv("RAG_ENV", "prod")
    monkeypatch.setenv("RAG_EMAIL_PROVIDER", "log")
    monkeypatch.setenv("RAG_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.mark.usefixtures("_log_in_prod")
async def test_api_refuses_to_start_with_the_log_provider_in_prod() -> None:
    """Checked before any engine or client is built, so no infrastructure is needed."""
    from fastapi import FastAPI

    from callobuzz_rag.api.app import lifespan

    with pytest.raises(RuntimeError, match="refused when RAG_ENV=prod"):
        async with lifespan(FastAPI()):
            pass  # pragma: no cover


@pytest.mark.usefixtures("_log_in_prod")
def test_worker_refuses_to_import_its_email_task_with_the_log_provider_in_prod() -> None:
    import importlib
    import sys

    import callobuzz_rag.worker.email_tasks as module

    saved = dict(vars(module))  # a failed reload leaves half-rebound globals behind
    try:
        with pytest.raises(RuntimeError, match="refused when RAG_ENV=prod"):
            importlib.reload(module)
    finally:
        vars(module).update(saved)
        sys.modules["callobuzz_rag.worker.email_tasks"] = module
