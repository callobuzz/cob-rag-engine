"""Nothing leaves a demo server: no e-mail, webhook or WhatsApp request."""

import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.demo import mode as demo_mode


def _demo(**over: object) -> Settings:
    base: dict[str, object] = {
        "demo_mode": True,
        "superadmin_email": "op@sahayak.example.com",
        "demo_owner_email": "owner@nirmalclinic.example.com",
        "demo_password": "Nirmal-Clinic-Demo-2026",
        "admin_api_key": "adm_" + "x" * 40,
        "session_secret": "s" * 48,
        "smtp_host": "smtp.example.com",
        "smtp_from": "Sahayak Studio <no-reply@sahayak.example.com>",
    }
    return Settings(**{**base, **over})  # type: ignore[arg-type]


def test_email_is_disabled_even_with_smtp() -> None:
    from callobuzz_rag.accounts.email.factory import email_configured, resolve_provider_name

    assert resolve_provider_name(_demo()) == "disabled"
    assert email_configured(_demo()) is False
    assert resolve_provider_name(_demo(demo_mode=False)) == "smtp"
    # even an explicit provider choice cannot turn it back on
    assert resolve_provider_name(_demo(email_provider="smtp")) == "disabled"


def test_enqueue_email_queues_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from callobuzz_rag.accounts.email import dispatch
    from callobuzz_rag.worker.email_tasks import send_email_task

    monkeypatch.setattr(demo_mode, "is_demo", lambda: True)
    with patch.object(send_email_task, "apply_async") as apply_async:
        dispatch.enqueue_email(
            "verify_code",
            "owner@nirmalclinic.example.com",
            {"name": "Asha", "code": "482913", "minutes": 10},
            expires_s=60,
        )
    apply_async.assert_not_called()


def test_enqueue_email_still_validates_in_a_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    from callobuzz_rag.accounts.email import dispatch
    from callobuzz_rag.core.exceptions import EmailTemplateError

    monkeypatch.setattr(demo_mode, "is_demo", lambda: True)
    with pytest.raises(EmailTemplateError):
        dispatch.enqueue_email("no-such-template", "a@b.in", {}, expires_s=60)


def test_enqueue_email_queues_when_not_a_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    from callobuzz_rag.accounts.email import dispatch
    from callobuzz_rag.worker.email_tasks import send_email_task

    monkeypatch.setattr(demo_mode, "is_demo", lambda: False)
    with patch.object(send_email_task, "apply_async") as apply_async:
        dispatch.enqueue_email(
            "verify_code",
            "owner@nirmalclinic.example.com",
            {"name": "Asha", "code": "482913", "minutes": 10},
            expires_s=60,
        )
    apply_async.assert_called_once()


def _no_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*a: object, **k: object) -> None:
        raise AssertionError("a request was made")

    monkeypatch.setattr(httpx.AsyncClient, "send", boom)


async def test_webhooks_send_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from callobuzz_rag.core import webhooks
    from callobuzz_rag.core.events import WebhookEvent, WebhookPayload

    _no_requests(monkeypatch)
    dispatcher = webhooks.WebhookDispatcher(_demo(), session_factory=None)
    payload = WebhookPayload(event=WebhookEvent.CONFIG_CHANGED, data={})
    assert await dispatcher.deliver_once("https://hooks.example.com/x", "s" * 16, payload) is None
    # dispatch would need the database to find subscribers: it must not even try
    await dispatcher.dispatch(WebhookEvent.CONFIG_CHANGED, {})


async def test_webhooks_still_send_when_not_a_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    from callobuzz_rag.core import webhooks
    from callobuzz_rag.core.events import WebhookEvent, WebhookPayload

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        webhooks.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    dispatcher = webhooks.WebhookDispatcher(_demo(demo_mode=False), session_factory=None)
    payload = WebhookPayload(event=WebhookEvent.CONFIG_CHANGED, data={})
    await dispatcher.deliver_once("https://hooks.example.com/x", "s" * 16, payload)
    assert seen == ["https://hooks.example.com/x"]


@dataclass(frozen=True)
class _GraphSettings:
    whatsapp_graph_url: str = "https://graph.facebook.com"
    whatsapp_graph_version: str = "v23.0"
    whatsapp_app_id: str = "555"
    whatsapp_app_secret: str = "app-secret-for-tests"


async def test_whatsapp_graph_refuses_before_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    from callobuzz_rag.channels.whatsapp import graph

    def fail(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a request was made")

    monkeypatch.setattr(demo_mode, "is_demo", lambda: True)
    client = graph.GraphClient(settings=_GraphSettings(), transport=httpx.MockTransport(fail))
    with pytest.raises(graph.GraphError) as err:
        await client.get_number("123456789012345", token="t")
    assert err.value.maybe_sent is False
    assert "demo mode" in str(err.value)
    with pytest.raises(graph.GraphError):
        await client.send_text("123456789012345", token="t", to="919822012345", text="hi")


@pytest.mark.parametrize("which", ["lead", "handoff"])
async def test_corpus_webhook_deliveries_are_skipped(
    monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    from callobuzz_rag.worker import conversation_tasks as tasks

    _no_requests(monkeypatch)
    monkeypatch.setattr("callobuzz_rag.core.config.get_settings", _demo)
    factory = AsyncMock(side_effect=AssertionError("the database was opened"))
    monkeypatch.setattr(tasks, "_session_factory", factory)
    if which == "lead":
        out = await tasks._deliver_lead_webhook(uuid.uuid4(), uuid.uuid4())
    else:
        out = await tasks._deliver_handoff_webhook(uuid.uuid4(), uuid.uuid4(), [], "")
    assert out == "skipped_demo"
    factory.assert_not_called()
