import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.api.app import create_app
from callobuzz_rag.api.deps import get_session
from callobuzz_rag.conversation.leads.tokens import secret_hash
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.core.config import Settings

SITE = "https://shree-labs.in"
KEY = Fernet.generate_key().decode()
FORM = "lf_" + "a" * 16
# What the form provider stores: the form id and the notice version it was shown with.
SHOWN = f"{FORM}:{AssistantConfig().leads.consent.notice_version}"
HOOK = "callobuzz_rag.api.routes.leads."


def _app(encryption_key: str = KEY) -> Any:
    app = create_app()
    app.state.settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        admin_api_key="adm",
        encryption_key=encryption_key,
        public_url="https://rag.shree-labs.in",
    )
    app.state.widget_rate_limiter = SimpleNamespace(_hit=AsyncMock(return_value=(True, 0)))
    return app


def _world(
    app: Any,
    *,
    enabled: bool = True,
    leads_enabled: bool = True,
    form_ids: tuple[str, ...] = (SHOWN,),
) -> Any:
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
        id=uuid.uuid4(),
        language="en",
        page_url=SITE + "/tests",
        utm={"utm_source": "google"},
        assistant_state={},
    )
    raw = AssistantConfig().model_dump()
    raw["enabled"] = enabled
    raw["leads"]["enabled"] = leads_enabled
    runtime = SimpleNamespace(
        config=AssistantConfig.model_validate(raw), version=3, draft_revision=None, enabled=enabled
    )
    state = SimpleNamespace(
        lead_id=None, lead_form_ids=list(form_ids), lead_offers=1, lead_offer_reason=None
    )
    session = AsyncMock()

    async def override() -> AsyncIterator[Any]:
        yield session

    app.dependency_overrides[get_session] = override
    return SimpleNamespace(
        corpus=corpus, key=key, conversation=conversation, runtime=runtime, state=state,
        session=session,
    )  # fmt: skip


async def _post(app: Any, w: Any, body: dict[str, Any], *, session_found: bool = True) -> Any:
    saved = MagicMock()
    with (
        patch(HOOK + "_authorize_widget", AsyncMock(return_value=(w.key, w.corpus))),
        patch(
            HOOK + "resolve_widget_session",
            AsyncMock(return_value=w.conversation if session_found else None),
        ),
        patch(HOOK + "get_runtime_config", AsyncMock(return_value=w.runtime)),
        patch(HOOK + "load_state", return_value=w.state),
        patch(HOOK + "save_state", saved),
        patch(
            HOOK + "create_lead",
            AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4(), qualification=None)),
        ) as create,
        patch(HOOK + "queue_lead_notification") as notify,
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            res = await c.post(
                "/api/v1/widget/lead",
                json=body,
                headers={"X-Widget-Key": w.key.public_key, "Origin": SITE},
            )
    w.saved = saved
    return res, create, notify


def _body(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session": "ws_" + "s" * 40,
        "form_id": FORM,
        "fields": {"name": "Priya Kulkarni", "phone": "98765 43210"},
        "consent": True,
    }
    base.update(kw)
    return base


async def test_happy_path() -> None:
    app = _app()
    w = _world(app)
    res, create, notify = await _post(app, w, _body())
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "received"
    assert body["confirmation"]["withdraw_url"].startswith(
        "https://rag.shree-labs.in/privacy/withdraw#t="
    )
    new = create.await_args.args[3]
    assert new.clean.phone == "+919876543210" and new.trigger == "lead_form"
    assert new.page_url == SITE + "/tests" and new.ip
    assert new.channel == "widget" and new.utm == {"utm_source": "google"}
    # the lead keeps only the hash of the link's secret; the link carries `<lead id>.<secret>`
    lead_part, _, secret = body["confirmation"]["withdraw_url"].split("#t=")[1].partition(".")
    assert (
        lead_part and new.withdraw_hash == secret_hash(secret) and secret not in new.withdraw_hash
    )
    notify.assert_called_once()
    assert w.state.lead_id is not None and w.state.lead_form_ids == []  # the form is used up
    w.saved.assert_called_once()
    w.session.commit.assert_awaited()
    w.session.refresh.assert_awaited_once()  # the conversation row is locked before the read
    assert w.session.refresh.await_args.kwargs == {"with_for_update": True}


async def test_a_second_submit_answers_the_same_without_a_second_lead() -> None:
    app = _app()
    w = _world(app)
    existing = SimpleNamespace(withdraw_hash="old", qualification=None)
    w.state.lead_id = str(uuid.uuid4())
    w.session.get = AsyncMock(return_value=existing)
    res, create, notify = await _post(app, w, _body())
    assert res.status_code == 201 and "withdraw#t=" in res.json()["confirmation"]["withdraw_url"]
    create.assert_not_awaited()
    notify.assert_not_called()
    # only a hash is kept, so the repeat answer carries a fresh link whose hash is stored
    token = res.json()["confirmation"]["withdraw_url"].split("#t=")[1]
    assert token.startswith(w.state.lead_id + ".")
    assert existing.withdraw_hash == secret_hash(token.split(".", 1)[1])
    w.session.commit.assert_awaited()


async def test_a_form_shown_with_an_older_notice_is_refused() -> None:
    """The owner republished the notice between the offer and the submit: the consent row
    would name wording the visitor never saw."""
    app = _app()
    w = _world(app, form_ids=(f"{FORM}:{AssistantConfig().leads.consent.notice_version + 1}",))
    res, create, _ = await _post(app, w, _body())
    assert (res.status_code, res.json()["code"]) == (400, "widget_session_invalid")
    create.assert_not_awaited()


async def test_honeypot_looks_successful_but_stores_nothing() -> None:
    app = _app()
    w = _world(app)
    res, create, notify = await _post(app, w, _body(website="http://spam.example"))
    assert res.status_code == 201 and res.json()["status"] == "received"
    assert "withdraw#t=" in res.json()["confirmation"]["withdraw_url"]
    create.assert_not_awaited()
    notify.assert_not_called()


@pytest.mark.parametrize(
    ("kwargs", "status", "code"),
    [
        ({"consent": False}, 422, "lead_consent_required"),
        ({"fields": {"phone": "12345"}}, 422, "lead_invalid_phone"),
        ({"fields": {"name": "x" * 130, "phone": "9876543210"}}, 422, "lead_field_invalid"),
        ({"form_id": "lf_" + "b" * 16}, 400, "widget_session_invalid"),
    ],
)
async def test_refusals(kwargs: dict[str, Any], status: int, code: str) -> None:
    app = _app()
    w = _world(app)
    res, create, _ = await _post(app, w, _body(**kwargs))
    assert res.status_code == status and res.json()["code"] == code
    create.assert_not_awaited()


async def test_validation_errors_say_which_field() -> None:
    app = _app()
    res, _, _ = await _post(app, _world(app), _body(fields={"phone": "12345"}))
    body = res.json()
    assert body["field"] == "phone" and body["reason"] == "format"


async def test_strict_body_refuses_unknown_fields() -> None:
    app = _app()
    res, create, _ = await _post(app, _world(app), {**_body(), "role": "admin"})
    assert res.status_code == 422
    create.assert_not_awaited()


async def test_session_disabled_and_no_key() -> None:
    app = _app()
    w = _world(app)
    res, _, _ = await _post(app, w, _body(), session_found=False)
    assert res.json()["code"] == "widget_session_invalid"
    res, _, _ = await _post(app, _world(app, enabled=False), _body())
    assert (res.status_code, res.json()["code"]) == (409, "assistant_disabled")
    res, _, _ = await _post(app, _world(app, leads_enabled=False), _body())
    assert (res.status_code, res.json()["code"]) == (409, "leads_disabled")
    app2 = _app(encryption_key="")
    res, _, _ = await _post(app2, _world(app2), _body())
    assert (res.status_code, res.json()["code"]) == (503, "encryption_not_configured")


async def test_rate_limited() -> None:
    app = _app()
    app.state.widget_rate_limiter._hit = AsyncMock(return_value=(False, 1800))
    res, create, _ = await _post(app, _world(app), _body())
    assert res.status_code == 429 and res.json()["code"] == "lead_rate_limited"
    assert res.headers["retry-after"] == "1800"
    create.assert_not_awaited()
    # keyed by widget key and visitor address, on the lead-specific budget
    key = app.state.widget_rate_limiter._hit.await_args.args
    assert key[0].startswith("widget:lead:") and key[1:] == (5, 3600)


async def test_a_broken_limiter_fails_open() -> None:
    app = _app()
    app.state.widget_rate_limiter._hit = AsyncMock(side_effect=ConnectionError("redis down"))
    res, create, _ = await _post(app, _world(app), _body())
    assert res.status_code == 201
    create.assert_awaited_once()


async def test_leads_do_not_count_as_questions() -> None:
    """No usage row and no cap check: nothing here may import the metering helpers."""
    import callobuzz_rag.api.routes.leads as module

    assert not hasattr(module, "record_usage") and not hasattr(module, "enforce_question_cap")
