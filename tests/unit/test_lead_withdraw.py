import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.api.app import create_app
from callobuzz_rag.api.deps import get_session
from callobuzz_rag.conversation.leads.tokens import (
    make_withdraw_token,
    new_withdraw_secret,
    secret_hash,
)
from callobuzz_rag.core.config import Settings

KEY = Fernet.generate_key().decode()
HOOK = "callobuzz_rag.api.routes.leads."


def _app(encryption_key: str = KEY) -> Any:
    app = create_app()
    app.state.settings = Settings(
        _env_file=None, admin_api_key="adm", encryption_key=encryption_key
    )  # type: ignore[call-arg]
    app.state.widget_rate_limiter = SimpleNamespace(_hit=AsyncMock(return_value=(True, 0)))

    async def nothing() -> AsyncIterator[Any]:
        yield AsyncMock()

    app.dependency_overrides[get_session] = nothing  # a test that needs rows overrides it again
    return app


async def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_page_and_script_are_public_and_have_no_inline_script() -> None:
    app = _app()
    async with await _client(app) as c:
        page = await c.get("/privacy/withdraw")
        script = await c.get("/privacy/withdraw.js")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert page.headers["referrer-policy"] == "no-referrer"
    assert '<script src="/privacy/withdraw.js"></script>' in page.text
    assert page.text.count("<script") == 1
    assert "cob-rag-engine by Callobuzz" in page.text
    assert script.status_code == 200 and "location.hash" in script.text
    assert "localStorage" not in script.text and "eval(" not in script.text


def _lead(secret: str, **kw: Any) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(),
        corpus_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        withdraw_hash=secret_hash(secret),
        **kw,
    )


def _with_session(app: Any, session: Any) -> None:
    async def override() -> AsyncIterator[Any]:
        yield session

    app.dependency_overrides[get_session] = override


async def test_withdraw_erases_and_is_idempotent() -> None:
    app = _app()
    secret = new_withdraw_secret()
    lead = _lead(secret)
    token = make_withdraw_token(lead.id, secret)
    session = AsyncMock()
    session.get = AsyncMock(side_effect=[lead, None])
    _with_session(app, session)
    with (
        patch(HOOK + "erase_lead", AsyncMock()) as erase,
        patch(HOOK + "record_audit", AsyncMock()) as audit,
    ):
        async with await _client(app) as c:
            first = await c.post("/api/v1/public/leads/withdraw", json={"token": token})
            second = await c.post("/api/v1/public/leads/withdraw", json={"token": token})
    assert first.status_code == second.status_code == 200
    assert first.json() == {"status": "withdrawn"}
    erase.assert_awaited_once()
    assert erase.await_args.kwargs["withdrawn"] is True
    assert audit.await_args.args[2] == "lead.withdrawn"
    assert audit.await_args.kwargs["actor"] == ("system", None, "visitor withdrawal")
    assert audit.await_args.kwargs["details"]["corpus_id"] == str(lead.corpus_id)
    assert "9876543210" not in str(audit.await_args)  # no contact data in the audit row


async def test_withdraw_works_after_the_encryption_key_is_rotated_away() -> None:
    """The link is a secret whose hash the lead keeps: it needs no RAG_ENCRYPTION_KEY at all,
    so the documented rotation (old key dropped) cannot invalidate links already given out."""
    app = _app(encryption_key="")
    secret = new_withdraw_secret()
    lead = _lead(secret)
    session = AsyncMock()
    session.get = AsyncMock(return_value=lead)
    _with_session(app, session)
    with (
        patch(HOOK + "erase_lead", AsyncMock()) as erase,
        patch(HOOK + "record_audit", AsyncMock()),
    ):
        async with await _client(app) as c:
            res = await c.post(
                "/api/v1/public/leads/withdraw",
                json={"token": make_withdraw_token(lead.id, secret)},
            )
    assert res.status_code == 200
    erase.assert_awaited_once()


async def test_a_wrong_secret_does_nothing() -> None:
    app = _app()
    lead = _lead(new_withdraw_secret())
    session = AsyncMock()
    session.get = AsyncMock(return_value=lead)
    _with_session(app, session)
    with patch(HOOK + "erase_lead", AsyncMock()) as erase:
        async with await _client(app) as c:
            res = await c.post(
                "/api/v1/public/leads/withdraw",
                json={"token": make_withdraw_token(lead.id, new_withdraw_secret())},
            )
    assert res.status_code == 400 and res.json()["code"] == "withdraw_token_invalid"
    erase.assert_not_awaited()


async def test_a_lead_with_no_hash_cannot_be_withdrawn_by_a_guess() -> None:
    app = _app()
    lead = _lead("x")
    lead.withdraw_hash = None
    session = AsyncMock()
    session.get = AsyncMock(return_value=lead)
    _with_session(app, session)
    with patch(HOOK + "erase_lead", AsyncMock()) as erase:
        async with await _client(app) as c:
            res = await c.post(
                "/api/v1/public/leads/withdraw",
                json={"token": make_withdraw_token(lead.id, "guess" * 8)},
            )
    assert res.status_code == 400
    erase.assert_not_awaited()


@pytest.mark.parametrize("token", ["x" * 40, f"{uuid.uuid4()}.", "notauuid." + "y" * 40])
async def test_bad_token(token: str) -> None:
    async with await _client(_app()) as c:
        res = await c.post("/api/v1/public/leads/withdraw", json={"token": token})
    assert res.status_code == 400 and res.json()["code"] == "withdraw_token_invalid"


async def test_rate_limit() -> None:
    app = _app()
    app.state.widget_rate_limiter._hit = AsyncMock(return_value=(False, 900))
    async with await _client(app) as c:
        res = await c.post("/api/v1/public/leads/withdraw", json={"token": "t" * 40})
    assert res.status_code == 429 and res.headers["retry-after"] == "900"
    assert app.state.widget_rate_limiter._hit.await_args.args[1:] == (10, 3600)


async def test_strict_body() -> None:
    async with await _client(_app()) as c:
        res = await c.post(
            "/api/v1/public/leads/withdraw", json={"token": "t" * 40, "lead_id": "x"}
        )
    assert res.status_code == 422
