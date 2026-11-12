"""Who may reach which knowledge base's WhatsApp chats: the same matrix as every scoped route (TV6)."""

import uuid
from typing import Any

import pytest
from cryptography.fernet import Fernet

from tests.unit.scope_harness import MATRIX, call, check, expected, scoped_app

T = uuid.uuid4()
ROUTES = [
    ("GET", "/api/v1/corpora/{c}/whatsapp/threads", None),
    ("GET", "/api/v1/corpora/{c}/whatsapp/threads/" + str(T), None),
    ("POST", "/api/v1/corpora/{c}/whatsapp/threads/" + str(T) + "/reply", {"text": "Hello"}),
    ("PATCH", "/api/v1/corpora/{c}/whatsapp/threads/" + str(T), {"bot_paused": True}),
    ("GET", "/api/v1/corpora/{c}/whatsapp", None),
    (
        "POST",
        "/api/v1/corpora/{c}/whatsapp/embedded-signup",
        {"code": "c", "phone_number_id": "1234567890", "waba_id": "5550001"},
    ),
    ("PATCH", "/api/v1/corpora/{c}/whatsapp", {"human_pause_hours": 6}),
    ("PUT", "/api/v1/corpora/{c}/whatsapp/documents", {"document_ids": []}),
    ("DELETE", "/api/v1/corpora/{c}/whatsapp", None),
    ("POST", "/api/v1/corpora/{c}/whatsapp/check", None),
]


def _with_key(app: Any) -> None:
    app.state.settings = app.state.settings.model_copy(
        update={"encryption_key": Fernet.generate_key().decode()}
    )


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
@pytest.mark.parametrize(("key", "which", "outcome"), MATRIX)
async def test_scoping(
    method: str, path: str, body: Any, key: str | None, which: str, outcome: str
) -> None:
    with scoped_app() as (app, world):
        _with_key(app)
        url = path.format(c=world.corpus(which).id)
        res = await call(app, key, method, url, json=body)
    check(expected(key, outcome, method, url, body), res.status_code, res.text)
