"""The handoff card ships in widget.js, and the visitor messages the widget sends to ask for a
person are understood as exactly that without a model call (C5)."""

import re
from importlib import resources
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.api.app import create_app
from callobuzz_rag.api.routes.widget import WIDGET_PARTS
from callobuzz_rag.conversation.rules import rule_labeler
from callobuzz_rag.conversation.types import TurnLabel
from callobuzz_rag.core.config import Settings
from callobuzz_rag.widget.security import LimitResult

STATIC = resources.files("callobuzz_rag.widget").joinpath("static")


@pytest.fixture
def app():
    application = create_app()
    application.state.settings = Settings(_env_file=None)
    application.state.widget_rate_limiter = SimpleNamespace(
        check=AsyncMock(return_value=LimitResult(True))
    )
    return application


async def test_widget_js_serves_the_handoff_helpers_before_the_widget(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/widget.js")
    body = res.text
    assert WIDGET_PARTS.index("static/handoff.js") < WIDGET_PARTS.index("static/widget.js")
    assert "CobHandoff" in body and "/api/v1/widget/handoff" in body
    assert body.index("root.CobHandoff") < body.index("attachShadow")
    assert "cob-rag-engine by Callobuzz" in body  # the footer line stays


def _asks() -> list[str]:
    source = STATIC.joinpath("widget.js").read_text("utf-8")
    block = re.search(r"var HANDOFF_ASKS = \{(.*?)\};", source, re.S)
    assert block is not None
    return re.findall(r':\s*"([^"]+)"', block.group(1))


def test_every_ask_of_the_widget_is_a_human_request() -> None:
    asks = _asks()
    assert len(asks) == 3  # English, Hindi, Hinglish
    for text in asks:
        assert rule_labeler().classify(text).label is TurnLabel.HUMAN_REQUEST, text


def test_the_card_never_uses_innerhtml_on_anything_but_its_own_escaped_markup() -> None:
    """handoff.js escapes every value; widget.js sets the card from that string only."""
    handoff = STATIC.joinpath("handoff.js").read_text("utf-8")
    assert "innerHTML" not in handoff and "eval(" not in handoff
    widget = STATIC.joinpath("widget.js").read_text("utf-8")
    assert widget.count("holder.innerHTML = window.CobHandoff.cardHtml(") == 1
