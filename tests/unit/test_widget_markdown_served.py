"""GET /widget.js carries the Markdown renderer in front of the widget, in one response."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from callobuzz_rag.api.app import create_app
from callobuzz_rag.api.routes.widget import WIDGET_PARTS
from callobuzz_rag.core.config import Settings
from callobuzz_rag.widget.security import LimitResult


@pytest.fixture
def app():
    application = create_app()
    application.state.settings = Settings(_env_file=None)
    application.state.widget_rate_limiter = SimpleNamespace(
        check=AsyncMock(return_value=LimitResult(True))
    )
    return application


async def test_widget_js_serves_markdown_first_and_keeps_powered_by(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/widget.js")
    assert res.status_code == 200
    body = res.text
    assert WIDGET_PARTS[0].endswith("markdown.js")
    assert "window.CobMd" in body or "g.CobMd" in body
    assert body.index("CobMd = ") < body.index("attachShadow")
    assert "Powered" in body or "POWERED_TEXT" in body  # the footer line is still in the script


def test_widget_script_never_parses_model_text_as_html() -> None:
    """No path from an answer to innerHTML: renderer builds nodes, widget sets text."""
    from importlib import resources

    package = resources.files("callobuzz_rag.widget")
    renderer = package.joinpath("static/markdown.js").read_text("utf-8")
    for banned in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
    ):
        assert banned not in renderer, banned
    widget = package.joinpath("static/widget.js").read_text("utf-8")
    # The only innerHTML left in the widget builds its own fixed template (icons, labels from
    # config that are escaped) and the user's own escaped text; answers go through CobMd.
    assert "renderMarkdown" not in widget
    assert "MD.stream" in widget


async def test_widget_js_skips_missing_parts_and_separates_parts_with_a_semicolon(
    app, monkeypatch
) -> None:
    """A part that does not exist yet is skipped, and parts never fuse into one expression."""
    from callobuzz_rag.api.routes import widget as widget_routes

    monkeypatch.setattr(widget_routes, "WIDGET_PARTS", (*WIDGET_PARTS, "static/not-written-yet.js"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/widget.js")
    assert res.status_code == 200
    assert "\n;\n" in res.text
    assert "attachShadow" in res.text
