"""Unit tests for the outbound webhook system."""

import hashlib
import hmac
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from callobuzz_rag.core.events import WebhookEvent, WebhookPayload
from callobuzz_rag.core.webhooks import WebhookDispatcher


class TestWebhookEvent:
    def test_all_events_have_dot_separated_values(self) -> None:
        for event in WebhookEvent:
            assert "." in event.value

    def test_event_count(self) -> None:
        assert len(WebhookEvent) == 11  # PIP3: fair_use.limit_reached; P1: billing.payment_received

    def test_eval_score_drop(self) -> None:
        assert WebhookEvent.EVAL_SCORE_DROP == "eval.score_drop"

    def test_knowledge_gap_detected(self) -> None:
        assert WebhookEvent.KNOWLEDGE_GAP_DETECTED == "knowledge_gap.detected"

    def test_optimization_complete(self) -> None:
        assert WebhookEvent.OPTIMIZATION_COMPLETE == "optimization.complete"

    def test_config_changed(self) -> None:
        assert WebhookEvent.CONFIG_CHANGED == "config.changed"

    def test_lead_and_handoff_events_are_corpus_only(self) -> None:
        from callobuzz_rag.core.events import CORPUS_ONLY_EVENTS

        assert WebhookEvent.LEAD_CREATED == "lead.created"
        assert WebhookEvent.HANDOFF_REQUESTED == "handoff.requested"
        assert {WebhookEvent.LEAD_CREATED, WebhookEvent.HANDOFF_REQUESTED} == CORPUS_ONLY_EVENTS


class TestWebhookPayload:
    def test_to_dict_structure(self) -> None:
        payload = WebhookPayload(
            event=WebhookEvent.CONFIG_CHANGED,
            data={"key": "value"},
        )
        d = payload.to_dict()
        assert d["event"] == "config.changed"
        assert d["data"] == {"key": "value"}
        assert "timestamp" in d

    def test_timestamp_auto_generated(self) -> None:
        payload = WebhookPayload(event=WebhookEvent.CONFIG_CHANGED)
        assert payload.timestamp != ""
        dt = datetime.fromisoformat(payload.timestamp)
        assert dt.tzinfo is not None

    def test_frozen(self) -> None:
        payload = WebhookPayload(event=WebhookEvent.CONFIG_CHANGED)
        with pytest.raises(AttributeError):
            payload.event = WebhookEvent.EVAL_SCORE_DROP  # type: ignore[misc]

    def test_empty_data_default(self) -> None:
        payload = WebhookPayload(event=WebhookEvent.CONFIG_CHANGED)
        assert payload.data == {}


class TestHMACSigning:
    def test_sign_produces_hex_string(self) -> None:
        sig = WebhookDispatcher._sign("test body", "secret123")
        assert isinstance(sig, str)
        assert len(sig) == 64

    def test_sign_deterministic(self) -> None:
        sig1 = WebhookDispatcher._sign("payload", "key")
        sig2 = WebhookDispatcher._sign("payload", "key")
        assert sig1 == sig2

    def test_sign_different_secrets_differ(self) -> None:
        sig1 = WebhookDispatcher._sign("payload", "key1")
        sig2 = WebhookDispatcher._sign("payload", "key2")
        assert sig1 != sig2

    def test_sign_matches_stdlib(self) -> None:
        body = '{"event":"config.changed"}'
        secret = "my-webhook-secret"
        expected = hmac.new(
            secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert WebhookDispatcher._sign(body, secret) == expected


def _make_settings(**overrides: object) -> MagicMock:
    settings = MagicMock()
    settings.webhook_timeout = overrides.get("webhook_timeout", 10.0)
    settings.webhook_max_retries = overrides.get("webhook_max_retries", 3)
    settings.webhook_retry_delay = overrides.get("webhook_retry_delay", 1.0)
    return settings


def _make_webhook_config(
    url: str = "https://example.com/hook",
    events: list[str] | None = None,
    secret: str = "test-secret-min-16ch",
    enabled: bool = True,
) -> MagicMock:
    config = MagicMock()
    config.id = uuid4()
    config.url = url
    config.events = events or ["config.changed"]
    config.secret = secret
    config.enabled = enabled
    config.corpus_id = None  # an engine-wide webhook
    return config


class TestWebhookDispatcher:
    @pytest.mark.asyncio
    async def test_dispatch_no_subscribers_does_not_crash(self) -> None:
        settings = _make_settings()
        session_factory = AsyncMock()
        dispatcher = WebhookDispatcher(settings, session_factory)

        with patch.object(dispatcher, "_get_subscribers", return_value=[]):
            await dispatcher.dispatch(WebhookEvent.CONFIG_CHANGED, {"key": "val"})

    @pytest.mark.asyncio
    async def test_dispatch_calls_deliver_for_each_subscriber(self) -> None:
        settings = _make_settings()
        session_factory = AsyncMock()
        dispatcher = WebhookDispatcher(settings, session_factory)

        configs = [_make_webhook_config(url=f"https://example.com/hook{i}") for i in range(3)]

        with (
            patch.object(dispatcher, "_get_subscribers", return_value=configs),
            patch.object(dispatcher, "_deliver", new_callable=AsyncMock) as mock_deliver,
        ):
            await dispatcher.dispatch(WebhookEvent.CONFIG_CHANGED, {"key": "val"})
            assert mock_deliver.call_count == 3

    @pytest.mark.asyncio
    async def test_dispatch_swallows_delivery_errors(self) -> None:
        settings = _make_settings()
        session_factory = AsyncMock()
        dispatcher = WebhookDispatcher(settings, session_factory)

        configs = [_make_webhook_config()]

        with (
            patch.object(dispatcher, "_get_subscribers", return_value=configs),
            patch.object(
                dispatcher,
                "_deliver",
                new_callable=AsyncMock,
                side_effect=httpx.ConnectError("refused"),
            ),
        ):
            await dispatcher.dispatch(WebhookEvent.CONFIG_CHANGED, {})

    @pytest.mark.asyncio
    async def test_dispatch_swallows_subscriber_fetch_errors(self) -> None:
        settings = _make_settings()
        session_factory = AsyncMock()
        dispatcher = WebhookDispatcher(settings, session_factory)

        with patch.object(
            dispatcher,
            "_get_subscribers",
            side_effect=Exception("DB down"),
        ):
            await dispatcher.dispatch(WebhookEvent.CONFIG_CHANGED, {})


class TestWebhookTestRoute:
    """``POST /webhooks/{id}/test`` turns every delivery failure into a 502."""

    @staticmethod
    async def _call(error: Exception) -> Exception:
        from fastapi import HTTPException

        from callobuzz_rag.api.routes.webhooks import test_webhook

        webhook = _make_webhook_config(url="http://10.0.0.5:9000/internal-hook")
        session = AsyncMock()
        session.get.return_value = webhook
        with (
            patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock, side_effect=error),
            pytest.raises(HTTPException) as exc_info,
        ):
            await test_webhook(webhook.id, session=session, _=None)
        return exc_info.value

    @pytest.mark.parametrize(
        "error",
        [
            httpx.ReadTimeout("timed out reading from 10.0.0.5:9000"),
            httpx.ConnectTimeout("connect to 10.0.0.5:9000 timed out"),
            httpx.RemoteProtocolError("peer 10.0.0.5 closed the connection"),
            httpx.TooManyRedirects("too many redirects via 10.0.0.5"),
        ],
    )
    async def test_any_httpx_error_is_a_502_naming_the_error_class(
        self, error: httpx.HTTPError
    ) -> None:
        exc = await self._call(error)
        assert exc.status_code == 502  # type: ignore[attr-defined]
        detail = exc.detail  # type: ignore[attr-defined]
        assert isinstance(detail, str)
        assert type(error).__name__ in detail
        assert "10.0.0.5" not in detail

    async def test_status_error_keeps_its_message(self) -> None:
        request = httpx.Request("POST", "https://example.com/hook")
        response = httpx.Response(503, request=request)
        exc = await self._call(httpx.HTTPStatusError("bad", request=request, response=response))
        assert exc.status_code == 502  # type: ignore[attr-defined]
        assert exc.detail == "Webhook endpoint returned 503"  # type: ignore[attr-defined]


class TestWebhookSchemas:
    def test_create_request_requires_min_secret_length(self) -> None:
        from pydantic import ValidationError

        from callobuzz_rag.api.schemas.webhooks import WebhookCreateRequest

        with pytest.raises(ValidationError, match="secret"):
            WebhookCreateRequest(
                url="https://example.com/hook",
                events=[WebhookEvent.CONFIG_CHANGED],
                secret="short",
            )

    def test_create_request_requires_at_least_one_event(self) -> None:
        from pydantic import ValidationError

        from callobuzz_rag.api.schemas.webhooks import WebhookCreateRequest

        with pytest.raises(ValidationError, match="events"):
            WebhookCreateRequest(
                url="https://example.com/hook",
                events=[],
                secret="a-valid-secret-key!!",
            )

    def test_create_request_valid(self) -> None:
        from callobuzz_rag.api.schemas.webhooks import WebhookCreateRequest

        req = WebhookCreateRequest(
            url="https://example.com/hook",
            events=[WebhookEvent.CONFIG_CHANGED, WebhookEvent.EVAL_SCORE_DROP],
            secret="a-valid-secret-key!!",
        )
        assert len(req.events) == 2
        assert str(req.url).rstrip("/") == "https://example.com/hook"


async def test_corpus_only_events_go_only_to_that_corpus() -> None:
    from callobuzz_rag.core.config import Settings

    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=result)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    d = WebhookDispatcher(Settings(_env_file=None), factory)  # type: ignore[call-arg]
    corpus = uuid4()
    await d._get_subscribers(WebhookEvent.LEAD_CREATED, corpus)
    sql = str(session.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "webhook_config.corpus_id = " in sql and "IS NULL" not in sql
    session.execute.reset_mock()
    assert await d._get_subscribers(WebhookEvent.LEAD_CREATED, None) == []
    session.execute.assert_not_awaited()
    await d._get_subscribers(WebhookEvent.CONFIG_CHANGED, None)
    sql = str(session.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "webhook_config.corpus_id IS NULL" in sql


async def test_deliver_once_posts_a_signed_body_without_retrying() -> None:
    from callobuzz_rag.core.config import Settings

    d = WebhookDispatcher(Settings(_env_file=None), MagicMock())  # type: ignore[call-arg]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500)

    real = httpx.AsyncClient
    with patch(
        "callobuzz_rag.core.webhooks.httpx.AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    ):
        payload = WebhookPayload(event=WebhookEvent.LEAD_CREATED, data={"a": 1})
        with pytest.raises(httpx.HTTPStatusError):
            await d.deliver_once("https://hooks.example.com/x", "secret", payload)
    assert len(seen) == 1  # no sleeping retries inside a worker slot
    body = seen[0].content.decode()
    expected = hmac.new(b"secret", body.encode(), hashlib.sha256).hexdigest()
    assert seen[0].headers["X-Webhook-Signature"] == expected
    assert seen[0].headers["X-Webhook-Event"] == "lead.created"
