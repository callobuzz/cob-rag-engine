"""The deployer's AI-disclosure lock reaches the prompt and the fixed first line (TV5)."""

from datetime import date
from typing import Any
from unittest.mock import AsyncMock

import pytest

from callobuzz_rag.conversation.compiler import CompileTurn, compile_prompt
from callobuzz_rag.conversation.disclosure import forced_line, read_trust
from tests.unit.conversation_helpers import make_config

LINE_EN = "I'm Asha, the AI assistant of Shree Labs."


def _config(on: bool, text: str | None = None) -> Any:
    identity: dict[str, Any] = {"ai_disclosure": on}
    if text is not None:
        identity["ai_disclosure_text"] = {"en": text}
    return make_config(identity=identity)


def test_the_prompt_says_nothing_when_the_owner_has_it_off_and_nothing_forces_it() -> None:
    prompt = compile_prompt(_config(False), CompileTurn(today=date(2026, 10, 6)))
    assert "in your first reply, say" not in prompt.system_prompt


def test_a_required_disclosure_is_in_the_prompt_even_when_the_owner_switched_it_off() -> None:
    turn = CompileTurn(today=date(2026, 10, 6), disclosure=LINE_EN)
    prompt = compile_prompt(_config(False), turn)
    assert f'in your first reply, say: "{LINE_EN}"' in prompt.system_prompt
    assert LINE_EN in prompt.speakable


def test_an_owner_who_has_it_on_is_unchanged_by_the_flag() -> None:
    plain = compile_prompt(_config(True), CompileTurn(today=date(2026, 10, 6)))
    forced = compile_prompt(_config(True), CompileTurn(today=date(2026, 10, 6), disclosure=LINE_EN))
    assert plain.system_prompt == forced.system_prompt


@pytest.mark.parametrize(
    ("required", "owner_on", "due"),
    [(True, False, True), (True, True, True), (False, True, True), (False, False, False)],
)
def test_the_line_is_due_when_the_deployer_requires_it_or_the_owner_has_it_on(
    required: bool, owner_on: bool, due: bool
) -> None:
    got = forced_line(_config(owner_on), "en", required=required, editable=True)
    assert got == (LINE_EN if due else None)


def test_the_owners_wording_needs_the_word_ai_and_the_deployers_permission() -> None:
    mine = "Hi, I'm Asha, an AI helper for Shree Labs."
    assert forced_line(_config(True, mine), "en", required=True, editable=True) == mine
    assert forced_line(_config(True, mine), "en", required=True, editable=False) == LINE_EN
    no_ai = "Hi, I'm Asha, your helper."
    assert forced_line(_config(True, no_ai), "en", required=True, editable=True) == LINE_EN


async def test_the_trust_setting_defaults_to_required_when_unreadable() -> None:
    from callobuzz_rag.accounts import settings_store

    original = settings_store.get_engine_settings
    settings_store.get_engine_settings = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[assignment]
    try:
        assert await read_trust(lambda: None) == (True, True)
    finally:
        settings_store.get_engine_settings = original  # type: ignore[assignment]


# ------------------------------------------------------------------ the director


@pytest.fixture
def _io(director_io: Any) -> Any:
    return director_io


def _system_prompt_of_the_answer(llm: Any) -> str:
    (call,) = [c for c in llm.calls if c[0] == "stream"]
    return str(call[1][0].content)


async def test_the_director_keeps_the_required_line_in_the_prompt_when_the_owner_is_off(
    _io: Any,
) -> None:
    from unittest.mock import patch

    from callobuzz_rag.accounts.settings_store import EngineSettings
    from tests.unit.conversation_helpers import FakeLLM, make_director, make_turn

    business = {"label": "business_question", "confidence": 0.9, "language": "en"}
    llm = FakeLLM(json_replies=[business], stream=["Home collection costs Rs 150 [1]."])
    with patch(
        "callobuzz_rag.accounts.settings_store.get_engine_settings",
        AsyncMock(return_value=EngineSettings()),
    ):
        await make_director(llm).run(
            make_turn("How much is home collection?", identity={"ai_disclosure": False})
        )
    assert f'say: "{LINE_EN}"' in _system_prompt_of_the_answer(llm)


async def test_the_director_leaves_the_prompt_alone_when_the_deployer_does_not_require_it(
    _io: Any,
) -> None:
    from unittest.mock import patch

    from callobuzz_rag.accounts.settings_store import EngineSettings
    from tests.unit.conversation_helpers import FakeLLM, make_director, make_turn

    business = {"label": "business_question", "confidence": 0.9, "language": "en"}
    llm = FakeLLM(json_replies=[business], stream=["Home collection costs Rs 150 [1]."])
    off = EngineSettings.model_validate({"trust": {"require_ai_disclosure": False}})
    with patch(
        "callobuzz_rag.accounts.settings_store.get_engine_settings", AsyncMock(return_value=off)
    ):
        await make_director(llm).run(
            make_turn("How much is home collection?", identity={"ai_disclosure": False})
        )
    assert "in your first reply, say" not in _system_prompt_of_the_answer(llm)
