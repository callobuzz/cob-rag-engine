"""TV5: the AI badge and first-line disclosure the deployer can lock."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.accounts.settings_store import EngineSettings
from callobuzz_rag.api.app import create_app
from callobuzz_rag.conversation import surfaces
from callobuzz_rag.conversation.schema import fill_persona
from callobuzz_rag.conversation.store import RuntimeConfig
from callobuzz_rag.conversation.surfaces import AI_BADGE, disclosure_line
from callobuzz_rag.widget.security import LimitResult
from tests.unit.conversation_helpers import make_config
from tests.unit.test_widget import SITE, _client, _key, _session, _settings, _use_session

pytestmark = [pytest.mark.usefixtures("unlimited_plan"), pytest.mark.assistant_on]


@pytest.fixture
def corpus() -> SimpleNamespace:
    import uuid

    cid = uuid.uuid4()
    return SimpleNamespace(
        id=cid,
        tenant_id=uuid.uuid4(),
        qdrant_collection=f"corpus_{str(cid)[:8]}",
        retrieval_config=None,
        llm_config=None,
        prompt_config=None,
    )


@pytest.fixture
def app() -> Any:
    application = create_app()
    application.state.settings = _settings()
    application.state.widget_rate_limiter = SimpleNamespace(
        check=AsyncMock(return_value=LimitResult(True))
    )
    return application


def _runtime(**cfg: Any) -> RuntimeConfig:
    return RuntimeConfig(config=make_config(**cfg), version=3, draft_revision=None, enabled=True)


async def _get_config(app: Any, corpus: Any, runtime: Any, trust: dict[str, Any]) -> Any:
    settings = EngineSettings.model_validate({"trust": trust})
    _use_session(app, _session(_key(corpus.id), corpus))
    with (
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=runtime)),
        patch(
            "callobuzz_rag.accounts.settings_store.get_engine_settings",
            AsyncMock(return_value=settings),
        ),
    ):
        async with await _client(app) as c:
            res = await c.get(
                "/api/v1/widget/config", headers={"X-Widget-Key": "pk_x", "Origin": SITE}
            )
    assert res.status_code == 200, res.text
    return res.json()


@pytest.mark.parametrize(
    ("required", "owner_on", "expect_badge", "expect_line"),
    [
        (True, True, True, True),
        (True, False, True, True),
        (False, True, True, True),
        (False, False, False, False),
    ],
)
async def test_badge_and_first_line(
    app: Any, corpus: Any, required: bool, owner_on: bool, expect_badge: bool, expect_line: bool
) -> None:
    runtime = _runtime(identity={"ai_disclosure": owner_on})
    cfg = await _get_config(app, corpus, runtime, {"require_ai_disclosure": required})
    assert (cfg["ai_badge"] is not None) is expect_badge
    assert (cfg["disclosure_line"] is not None) is expect_line
    assert cfg["disclosure_required"] is required


async def test_defaults_always_disclose(app: Any, corpus: Any) -> None:
    cfg = await _get_config(app, corpus, _runtime(identity={"ai_disclosure": False}), {})
    assert cfg["ai_badge"] == "AI assistant"
    assert "AI assistant" in cfg["disclosure_line"]
    assert cfg["disclosure_required"] is True


async def test_an_unreadable_engine_setting_still_discloses(app: Any, corpus: Any) -> None:
    _use_session(app, _session(_key(corpus.id), corpus))
    with (
        patch.object(
            surfaces,
            "assistant_for",
            AsyncMock(return_value=_runtime(identity={"ai_disclosure": False})),
        ),
        patch(
            "callobuzz_rag.accounts.settings_store.get_engine_settings",
            AsyncMock(side_effect=RuntimeError("down")),
        ),
    ):
        async with await _client(app) as c:
            res = await c.get(
                "/api/v1/widget/config", headers={"X-Widget-Key": "pk_x", "Origin": SITE}
            )
    assert res.json()["ai_badge"] == "AI assistant"
    assert res.json()["disclosure_required"] is True


async def test_no_assistant_but_required_still_shows_badge_and_line(app: Any, corpus: Any) -> None:
    cfg = await _get_config(app, corpus, None, {})
    assert cfg["ai_badge"] == "AI assistant"
    assert cfg["disclosure_line"] is not None
    assert "AI" in cfg["disclosure_line"]
    assert set(cfg["disclosure_lines"]) == {"en", "hi", "hi-Latn"}


async def test_no_assistant_and_not_required_shows_nothing(app: Any, corpus: Any) -> None:
    cfg = await _get_config(app, corpus, None, {"require_ai_disclosure": False})
    assert cfg["ai_badge"] is None
    assert cfg["disclosure_line"] is None


async def test_the_line_is_given_in_every_supported_language(app: Any, corpus: Any) -> None:
    runtime = _runtime(language={"primary": "en", "supported": ["en", "hi"]})
    cfg = await _get_config(app, corpus, runtime, {})
    assert set(cfg["disclosure_lines"]) == {"en", "hi"}
    assert cfg["disclosure_lines"]["en"] == cfg["disclosure_line"]
    assert cfg["disclosure_lines"]["hi"] != cfg["disclosure_lines"]["en"]


@pytest.mark.parametrize(
    ("owner_text", "editable", "used"),
    [
        ("Main Asha hoon, Shree Dental ki AI assistant.", True, "owner"),
        ("I'm Asha from Shree Dental. Ask me anything!", True, "default"),
        ("मैं आशा हूँ, एआई सहायक।", True, "owner"),
        ("Hi, I'm an ai helper.", True, "owner"),
        ("I'm Asha, the AI assistant of Shree Dental.", False, "default"),
        ("I'm SAIF, your helper", True, "default"),
    ],
)
def test_wording_rule(owner_text: str, editable: bool, used: str) -> None:
    cfg = make_config(identity={"ai_disclosure_text": {"en": owner_text, "hi": owner_text}})
    line = disclosure_line(cfg, "en", editable=editable)
    assert (line == fill_persona(owner_text, cfg)) is (used == "owner")
    if used == "default":
        assert "AI" in line


def test_badge_is_localised_chrome() -> None:
    assert AI_BADGE == {"en": "AI assistant", "hi": "एआई सहायक", "hi-Latn": "AI assistant"}
