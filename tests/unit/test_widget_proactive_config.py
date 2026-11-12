"""Widget config carries the proactive greetings (C9)."""

from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.store import RuntimeConfig
from tests.unit.test_widget import (  # noqa: F401
    SITE,
    _client,
    _key,
    _session,
    _use_session,
    app,
    corpus,
)

pytestmark = pytest.mark.usefixtures("unlimited_plan")
ASSISTANT_FOR = "callobuzz_rag.conversation.surfaces.assistant_for"
HEADERS = {"X-Widget-Key": "pk_" + "a" * 32, "Origin": SITE}

_CONFIG = AssistantConfig.model_validate(
    {
        "language": {"primary": "hi"},
        "conversation": {
            "proactive_greetings": [
                {
                    "id": "tests",
                    "page_url_glob": "/tests/**",
                    "delay_seconds": 10,
                    "message": {"en": "Need help choosing a test?", "hi": "टेस्ट चुनने में मदद चाहिए?"},
                },
                {"id": "off", "page_url_glob": "/**", "message": {"en": "x"}, "enabled": False},
                {
                    "id": "english-only",
                    "page_url_glob": "/offers/**",
                    "message": {"en": "Diwali offers are on."},
                    "frequency": "once_per_day",
                },
            ]
        },
    }
)


def _runtime(config: AssistantConfig) -> RuntimeConfig:
    return RuntimeConfig(config=config, version=3, draft_revision=None, enabled=True)


async def test_config_lists_enabled_greetings_in_the_primary_language(app, corpus) -> None:  # noqa: F811
    _use_session(app, _session(_key(corpus.id), corpus))
    with patch(ASSISTANT_FOR, AsyncMock(return_value=_runtime(_CONFIG))):
        async with await _client(app) as c:
            res = await c.get("/api/v1/widget/config", headers=HEADERS)
    body = res.json()
    assert body["language"] == "hi"
    assert body["proactive"] == [
        {
            "id": "tests",
            "page_url_glob": "/tests/**",
            "delay_seconds": 10,
            "message": "टेस्ट चुनने में मदद चाहिए?",
            "frequency": "once_per_session",
        },
        {
            "id": "english-only",
            "page_url_glob": "/offers/**",
            "delay_seconds": 15,
            "message": "Diwali offers are on.",
            "frequency": "once_per_day",
        },
    ]


async def test_assistant_off_means_no_greetings(app, corpus) -> None:  # noqa: F811
    _use_session(app, _session(_key(corpus.id), corpus))
    with patch(ASSISTANT_FOR, AsyncMock(return_value=None)):
        async with await _client(app) as c:
            res = await c.get("/api/v1/widget/config", headers=HEADERS)
    assert res.json()["proactive"] == []


async def test_script_bundles_core_helpers_between_markdown_and_the_widget(app) -> None:  # noqa: F811
    async with await _client(app) as c:
        res = await c.get("/widget.js")
    text = res.text
    assert text.index("CobMd") < text.index("CobWidgetCore") < text.index("attachShadow")
