"""The AI disclosure on WhatsApp (TV5): the deployer's lock applies there too, with a fixed line
and not only a prompt instruction the model may not obey."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.accounts.settings_store import EngineSettings
from tests.unit.conversation_helpers import make_config
from tests.unit.whatsapp_fixtures import inbound_text
from tests.unit.whatsapp_rig import TS, rig_for, state_of
from tests.unit.whatsapp_world import WhatsAppWorld

pytestmark = pytest.mark.assistant_on

LINE_EN = "I'm Asha, the AI assistant of Shree Labs."


@pytest.fixture
def world() -> WhatsAppWorld:
    return WhatsAppWorld()


def engine(**trust: Any) -> Any:
    return patch(
        "callobuzz_rag.accounts.settings_store.get_engine_settings",
        AsyncMock(return_value=EngineSettings.model_validate({"trust": trust})),
    )


def owner(on: bool, text: str | None = None) -> Any:
    identity: dict[str, Any] = {"ai_disclosure": on}
    if text is not None:
        identity["ai_disclosure_text"] = {"en": text}
    return make_config(identity=identity)


async def _ask(rig: Any, text: str = "How much is home collection?", n: int = 1) -> None:
    (thread_id,) = await rig.deliver(
        inbound_text(wamid=f"wamid.IN{n}", text=text, timestamp=TS + 10 * n)
    )
    await rig.run(thread_id)


async def test_the_first_reply_says_it_is_an_ai_when_the_deployer_requires_it(world) -> None:
    """The owner switched their own line off; the deployer's lock is on (the default)."""
    with engine(), rig_for(world, config=owner(False), disclosure=True) as rig:
        await _ask(rig)
    line, answer = rig.texts()  # the line is a message of its own, before the answer
    assert line == LINE_EN and "Rs 150" in answer and LINE_EN not in answer


async def test_the_line_is_said_once_per_chat_not_on_every_reply(world) -> None:
    with engine(), rig_for(world, config=owner(False), disclosure=True) as rig:
        await _ask(rig, n=1)
        await _ask(rig, "And the price of a CBC?", n=2)
    line, first, second = rig.texts()
    assert line == LINE_EN and LINE_EN not in first and LINE_EN not in second
    assert state_of(world).ai_disclosed is True


async def test_an_owner_who_has_it_on_gets_it_even_when_the_deployer_does_not_require_it(
    world,
) -> None:
    with (
        engine(require_ai_disclosure=False),
        rig_for(world, config=owner(True), disclosure=True) as rig,
    ):
        await _ask(rig)
    assert rig.texts()[0] == LINE_EN


async def test_nothing_is_added_when_neither_requires_it(world) -> None:
    with (
        engine(require_ai_disclosure=False),
        rig_for(world, config=owner(False), disclosure=True) as rig,
    ):
        await _ask(rig)
    (text,) = rig.texts()
    assert LINE_EN not in text and text.startswith("Home collection")
    assert state_of(world).ai_disclosed is False


async def test_the_owners_wording_is_used_only_when_the_deployer_lets_them(world) -> None:
    mine = "Hi, I'm Asha, an AI helper for Shree Labs."
    with engine(), rig_for(world, config=owner(True, mine), disclosure=True) as rig:
        await _ask(rig)
    assert rig.texts()[0] == mine
    world2 = WhatsAppWorld()
    with (
        engine(disclosure_wording_editable=False),
        rig_for(world2, config=owner(True, mine), disclosure=True) as rig2,
    ):
        await _ask(rig2)
    assert rig2.texts()[0] == LINE_EN and mine not in rig2.texts()


async def test_an_unreadable_engine_setting_keeps_the_line(world) -> None:
    broken = patch(
        "callobuzz_rag.accounts.settings_store.get_engine_settings",
        AsyncMock(side_effect=RuntimeError("database down")),
    )
    with broken, rig_for(world, config=owner(False), disclosure=True) as rig:
        await _ask(rig)
    assert rig.texts()[0] == LINE_EN
