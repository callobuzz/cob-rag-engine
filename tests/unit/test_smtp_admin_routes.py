"""GET /api/v1/admin/smtp and POST /api/v1/admin/smtp/test (accounts A2)."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.accounts.email.base import EmailProvider, OutgoingEmail
from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
from callobuzz_rag.api.routes import email_admin
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import EmailPermanentError, EmailTransientError
from tests.unit.scope_harness import ADMIN_KEY, call, scoped_app

PASSWORD = "smtp-password-never-shown"
USERNAME = "mailer-user-never-shown"


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    invalidate_engine_settings_cache()
    yield
    invalidate_engine_settings_cache()


def _settings(**kwargs: Any) -> Settings:
    return Settings(_env_file=None, admin_api_key=ADMIN_KEY, llm_api_key="k", **kwargs)


SMTP = {
    "smtp_host": "smtp.sahayak.example",
    "smtp_port": 465,
    "smtp_security": "ssl",
    "smtp_from": "Sahayak AI <no-reply@sahayak.example>",
    "smtp_username": USERNAME,
    "smtp_password": PASSWORD,
    "dashboard_url": "https://app.sahayak.example",
}


def _session_factory() -> MagicMock:
    row = MagicMock(value={"branding": {"product_name": "Sahayak AI"}})
    session = AsyncMock()
    session.get = AsyncMock(return_value=row)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return factory


class FakeProvider(EmailProvider):
    def __init__(self, name: str = "smtp", error: Exception | None = None) -> None:
        self.name = name
        self.error = error
        self.sent: list[OutgoingEmail] = []

    async def send(self, email: OutgoingEmail) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append(email)


async def _request(
    settings: Settings,
    method: str,
    path: str,
    key: str | None = "admin",
    provider: EmailProvider | None = None,
    **kwargs: Any,
) -> Any:
    with scoped_app() as (app, _):
        app.state.settings = settings
        app.state.session_factory = _session_factory()
        if provider is None:
            return await call(app, key, method, path, **kwargs)
        with patch.object(email_admin, "email_provider_from_settings", return_value=provider):
            return await call(app, key, method, path, **kwargs)


# ------------------------------------------------------------------ status


async def test_status_shape_without_secret_values() -> None:
    res = await _request(_settings(**SMTP), "GET", "/api/v1/admin/smtp")
    assert res.status_code == 200, res.text
    assert res.json() == {
        "provider": "smtp",
        "configured": True,
        "host": "smtp.sahayak.example",
        "port": 465,
        "security": "ssl",
        "from_address": "Sahayak AI <no-reply@sahayak.example>",
        "username_set": True,
        "password_set": True,
        "dashboard_url": "https://app.sahayak.example",
    }
    assert PASSWORD not in res.text and USERNAME not in res.text


async def test_status_in_development_without_smtp() -> None:
    res = await _request(_settings(), "GET", "/api/v1/admin/smtp")
    body = res.json()
    assert body["provider"] == "log" and body["configured"] is True
    assert body["host"] is None and body["from_address"] is None
    assert body["username_set"] is False and body["password_set"] is False


async def test_status_in_production_without_smtp() -> None:
    res = await _request(_settings(env="prod"), "GET", "/api/v1/admin/smtp")
    assert res.json()["provider"] == "disabled"
    assert res.json()["configured"] is False


# -------------------------------------------------------------------- test


async def test_send_with_a_fake_provider() -> None:
    provider = FakeProvider()
    res = await _request(
        _settings(**SMTP),
        "POST",
        "/api/v1/admin/smtp/test",
        provider=provider,
        json={"to": "me@shree.example"},
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"sent": True, "provider": "smtp", "to": "me@shree.example"}
    (email,) = provider.sent
    assert email.to == "me@shree.example"
    assert email.subject == "Sahayak AI test e-mail"
    assert "https://app.sahayak.example" in email.html
    assert email.text.rstrip().endswith("cob-rag-engine by Callobuzz")


async def test_send_with_the_log_provider(caplog: pytest.LogCaptureFixture) -> None:
    res = await _request(
        _settings(), "POST", "/api/v1/admin/smtp/test", json={"to": "me@shree.example"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["provider"] == "log"
    assert "DEV EMAIL to=me@shree.example" in caplog.text


async def test_disabled_is_503() -> None:
    res = await _request(
        _settings(env="prod"), "POST", "/api/v1/admin/smtp/test", json={"to": "me@shree.example"}
    )
    assert res.status_code == 503
    assert res.json() == {
        "detail": "E-mail is not configured (set RAG_SMTP_HOST)",
        "code": "email_not_configured",
    }


@pytest.mark.parametrize(
    ("error", "detail", "smtp_code", "name"),
    [
        (
            EmailPermanentError("x", smtp_code=535, error="SMTPAuthenticationError"),
            "The SMTP server refused the message",
            535,
            "SMTPAuthenticationError",
        ),
        (
            EmailTransientError("x", error="SMTPConnectError"),
            "The test e-mail could not be sent",
            None,
            "SMTPConnectError",
        ),
    ],
)
async def test_provider_error_is_502(
    error: Exception, detail: str, smtp_code: int | None, name: str
) -> None:
    res = await _request(
        _settings(**SMTP),
        "POST",
        "/api/v1/admin/smtp/test",
        provider=FakeProvider(error=error),
        json={"to": "me@shree.example"},
    )
    assert res.status_code == 502
    assert res.json() == {
        "detail": detail,
        "code": "smtp_test_failed",
        "smtp_code": smtp_code,
        "error": name,
    }


async def test_timeout_is_502() -> None:
    class Slow(EmailProvider):
        name = "smtp"

        async def send(self, email: OutgoingEmail) -> None:
            import asyncio

            await asyncio.sleep(5)

    with patch.object(email_admin, "TEST_TIMEOUT_S", 0.05):
        res = await _request(
            _settings(**SMTP),
            "POST",
            "/api/v1/admin/smtp/test",
            provider=Slow(),
            json={"to": "me@shree.example"},
        )
    assert res.status_code == 502
    assert res.json()["error"] == "TimeoutError"


@pytest.mark.parametrize(
    "body", [{"to": "not-an-address"}, {"to": "me@shree.example", "cc": "x@y.example"}, {}]
)
async def test_body_is_validated(body: dict[str, Any]) -> None:
    res = await _request(_settings(), "POST", "/api/v1/admin/smtp/test", json=body)
    assert res.status_code == 422


# -------------------------------------------------------------------- auth


@pytest.mark.parametrize("key", ["tk_a", "ck_a"])
@pytest.mark.parametrize(
    ("method", "path"), [("GET", "/api/v1/admin/smtp"), ("POST", "/api/v1/admin/smtp/test")]
)
async def test_tenant_and_corpus_keys_are_forbidden(key: str, method: str, path: str) -> None:
    provider = FakeProvider()
    res = await _request(
        _settings(**SMTP), method, path, key=key, provider=provider, json={"to": "a@b.example"}
    )
    assert res.status_code == 403
    assert not provider.sent


@pytest.mark.parametrize(
    ("method", "path"), [("GET", "/api/v1/admin/smtp"), ("POST", "/api/v1/admin/smtp/test")]
)
async def test_no_key_is_401(method: str, path: str) -> None:
    res = await _request(_settings(**SMTP), method, path, key=None, json={"to": "a@b.example"})
    assert res.status_code == 401
