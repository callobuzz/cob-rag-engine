"""A callback request through the lead form makes a lead tagged `callback` and a handoff (C5)."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.api.app import create_app
from callobuzz_rag.api.deps import get_session
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.core.config import Settings

SITE = "https://shree-labs.in"
FORM = "lf_" + "a" * 16
SHOWN = f"{FORM}:{AssistantConfig().leads.consent.notice_version}"
HOOK = "callobuzz_rag.api.routes.leads."


def _tomorrow() -> str:
    return (
        (datetime.now(UTC) + timedelta(days=1))
        .astimezone(ZoneInfo("Asia/Kolkata"))
        .date()
        .isoformat()
    )


def _world(config: AssistantConfig | None = None) -> Any:
    app = create_app()
    app.state.settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        admin_api_key="adm",
        encryption_key=Fernet.generate_key().decode(),
        public_url="https://rag.shree-labs.in",
    )
    app.state.widget_rate_limiter = SimpleNamespace(_hit=AsyncMock(return_value=(True, 0)))
    corpus = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="Patient help")
    key = SimpleNamespace(
        id=uuid.uuid4(),
        corpus_id=corpus.id,
        public_key="pk_" + "a" * 32,
        allowed_origins=[SITE],
        is_active=True,
        appearance={},
    )
    conversation = SimpleNamespace(
        id=uuid.uuid4(), language="en", page_url=SITE + "/tests", utm=None, assistant_state={}
    )
    runtime = SimpleNamespace(
        config=config or AssistantConfig(), version=3, draft_revision=None, enabled=True
    )
    session = AsyncMock()

    async def override() -> AsyncIterator[Any]:
        yield session

    app.dependency_overrides[get_session] = override
    state = ConversationState(lead_form_ids=[SHOWN], lead_offer_reason="callback")
    state.last_handoff_reason = "keyword"
    return SimpleNamespace(
        app=app, corpus=corpus, key=key, conversation=conversation, runtime=runtime,
        session=session, state=state,
    )  # fmt: skip


async def _post(w: Any, body: dict[str, Any]) -> tuple[Any, Any, Any, Any, Any]:
    lead = SimpleNamespace(id=uuid.uuid4(), qualification=None)
    handoff = SimpleNamespace(id=uuid.uuid4())
    with (
        patch(HOOK + "_authorize_widget", AsyncMock(return_value=(w.key, w.corpus))),
        patch(HOOK + "resolve_widget_session", AsyncMock(return_value=w.conversation)),
        patch(HOOK + "get_runtime_config", AsyncMock(return_value=w.runtime)),
        patch(HOOK + "load_state", return_value=w.state),
        patch(HOOK + "save_state", MagicMock()),
        patch(HOOK + "create_lead", AsyncMock(return_value=lead)) as create_lead,
        patch(HOOK + "create_handoff", AsyncMock(return_value=handoff)) as create_handoff,
        patch(HOOK + "queue_lead_notification") as notify_lead,
        patch(HOOK + "queue_handoff_notification") as notify_handoff,
    ):
        async with AsyncClient(transport=ASGITransport(app=w.app), base_url="http://test") as c:
            res = await c.post(
                "/api/v1/widget/lead",
                json=body,
                headers={"X-Widget-Key": w.key.public_key, "Origin": SITE},
            )
    return res, create_lead, create_handoff, notify_lead, (notify_handoff, lead, handoff)


def _body(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session": "ws_" + "s" * 40,
        "form_id": FORM,
        "fields": {"name": "Priya Kulkarni", "phone": "98765 43210"},
        "consent": True,
        "callback_window": {"date": _tomorrow(), "start": "16:00", "end": "18:00"},
    }
    base.update(kw)
    return base


async def test_callback_creates_a_tagged_lead_and_a_handoff() -> None:
    w = _world()
    res, create_lead, create_handoff, notify_lead, (notify_handoff, lead, handoff) = await _post(
        w, _body()
    )
    assert res.status_code == 201, res.text
    new = create_lead.await_args.args[3]
    assert new.tags == ["callback"] and new.trigger == "callback"
    assert new.callback_window == {"date": _tomorrow(), "start": "16:00", "end": "18:00"}
    kwargs = create_handoff.await_args.kwargs
    assert kwargs["channel"] == "callback" and kwargs["lead_id"] == lead.id
    assert kwargs["reason"] == "keyword" and kwargs["conversation_id"] == w.conversation.id
    notify_lead.assert_called_once_with(lead.id)  # the lead keeps its own notification
    notify_handoff.assert_called_once_with(handoff.id)
    assert w.state.handoff_ids == [str(handoff.id)]


async def test_a_plain_lead_makes_no_handoff() -> None:
    w = _world()
    body = _body()
    del body["callback_window"]
    res, create_lead, create_handoff, _, (notify_handoff, _, _) = await _post(w, body)
    assert res.status_code == 201
    new = create_lead.await_args.args[3]
    assert new.tags == [] and new.callback_window is None and new.trigger == "callback"
    create_handoff.assert_not_awaited()
    notify_handoff.assert_not_called()


async def test_a_window_in_the_past_or_inverted_is_422() -> None:
    w = _world()
    yesterday = (datetime.now(UTC) - timedelta(days=2)).date().isoformat()
    for window in (
        {"date": yesterday, "start": "10:00", "end": "11:00"},
        {"date": _tomorrow(), "start": "18:00", "end": "17:00"},
        {"date": _tomorrow(), "start": "4pm", "end": "18:00"},
    ):
        res, create_lead, create_handoff, _, _ = await _post(w, _body(callback_window=window))
        assert res.status_code == 422, res.text
        create_lead.assert_not_awaited()
        create_handoff.assert_not_awaited()
    res, *_ = await _post(
        w, _body(callback_window={"date": yesterday, "start": "10:00", "end": "11:00"})
    )
    assert res.json()["code"] == "callback_window_invalid"


async def test_callback_must_be_an_enabled_channel() -> None:
    cfg = AssistantConfig.model_validate({"handoff": {"channels": [{"kind": "whatsapp"}]}})
    w = _world(cfg)
    res, create_lead, _, _, _ = await _post(w, _body())
    assert res.status_code == 409
    assert (
        res.json()["code"] == "handoff_channel_unavailable" and res.json()["channel"] == "callback"
    )
    create_lead.assert_not_awaited()
    off = AssistantConfig.model_validate({"handoff": {"enabled": False}})
    res, *_ = await _post(_world(off), _body())
    assert res.json()["code"] == "handoff_channel_unavailable"


async def test_callback_is_taken_after_hours_too() -> None:
    """The form was offered while the callback channel was available; it is accepted at any hour."""
    cfg = AssistantConfig.model_validate(
        {"handoff": {"channels": [{"kind": "callback", "available": "closed_hours"}]}}
    )
    res, *_ = await _post(_world(cfg), _body())
    assert res.status_code == 201, res.text


async def test_a_second_submit_after_a_lead_makes_no_second_handoff() -> None:
    w = _world()
    w.state.lead_id = str(uuid.uuid4())
    w.session.get = AsyncMock(return_value=SimpleNamespace(withdraw_hash="old", qualification=None))
    res, create_lead, create_handoff, _, (notify_handoff, _, _) = await _post(w, _body())
    assert res.status_code == 201
    create_lead.assert_not_awaited()
    create_handoff.assert_not_awaited()
    notify_handoff.assert_not_called()
