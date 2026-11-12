"""Follow-ups: off by default, one template, only to a person who said yes (TV6)."""

from datetime import timedelta

import pytest

from callobuzz_rag.channels.whatsapp import followups
from callobuzz_rag.db.models.channel import ChannelThread
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.message import Message
from tests.unit.whatsapp_fixtures import inbound_button, inbound_text
from tests.unit.whatsapp_rig import NOW, TS, rig_for
from tests.unit.whatsapp_world import WhatsAppWorld

pytestmark = pytest.mark.assistant_on

ON = {
    "follow_up": {
        "enabled": True,
        "template_name": "callback_followup",
        "language": "en",
        "after_hours": 24,
    }
}


@pytest.fixture
def world() -> WhatsAppWorld:
    return WhatsAppWorld()


def thread_of(world: WhatsAppWorld) -> ChannelThread:
    return world.db.all(ChannelThread)[0]


def templates(rig) -> list:
    return [r for r in rig.sends() if r.json["type"] == "template"]


def optin_questions(rig) -> list:
    """The "May we message you here?" messages (not the assistant's own quick replies)."""
    out = []
    for r in rig.sends():
        if r.json["type"] != "interactive":
            continue
        ids = [b["reply"]["id"] for b in r.json["interactive"]["action"]["buttons"]]
        if "optin_yes" in ids:
            out.append(r)
    return out


async def ask_for_a_person(rig, world, wamid="wamid.H1"):
    (thread_id,) = await rig.deliver(
        inbound_text(wamid=wamid, text="Kisi insaan se baat karni hai")
    )
    await rig.run(thread_id)
    return thread_id


# ------------------------------------------------------------------ the opt-in question


async def test_the_opt_in_question_is_asked_once_after_a_handoff_when_follow_ups_are_on(
    world,
) -> None:
    world.connection.settings = ON
    with rig_for(world) as rig:
        thread_id = await ask_for_a_person(rig, world)
        buttons = optin_questions(rig)
        assert len(buttons) == 1
        ids = [b["reply"]["id"] for b in buttons[0].json["interactive"]["action"]["buttons"]]
        assert ids == ["optin_yes", "optin_no"]
        assert thread_of(world).opt_in_asked_at == NOW
        await rig.deliver(
            inbound_text(wamid="wamid.H2", text="Kisi insaan se baat karni hai", timestamp=TS + 5)
        )
        await rig.run(thread_id)
    assert len(optin_questions(rig)) == 1  # once per chat


async def test_no_question_when_follow_ups_are_off(world) -> None:
    with rig_for(world) as rig:
        await ask_for_a_person(rig, world)
    assert optin_questions(rig) == []
    assert thread_of(world).opt_in_asked_at is None


async def test_no_question_after_an_ordinary_answer(world) -> None:
    world.connection.settings = ON
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(inbound_text(text="How much is home collection?"))
        await rig.run(thread_id)
    assert optin_questions(rig) == []


async def test_no_question_for_someone_who_said_stop(world) -> None:
    world.connection.settings = ON
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.S", text="STOP"))
        await rig.run(thread_id)
        await rig.deliver(
            inbound_text(wamid="wamid.H", text="Kisi insaan se baat karni hai", timestamp=TS + 5)
        )
        await rig.run(thread_id)
    assert optin_questions(rig) == []


async def test_a_yes_is_recorded_and_a_no_is_not(world) -> None:
    world.connection.settings = ON
    with rig_for(world) as rig:
        thread_id = await ask_for_a_person(rig, world)
        await rig.deliver(inbound_button(wamid="wamid.Y", button_id="optin_yes", timestamp=TS + 10))
        await rig.run(thread_id)
        assert thread_of(world).opted_in_at is not None
        assert "we will message you here" in rig.texts()[-1]
        thread_of(world).opted_in_at = None
        await rig.deliver(inbound_button(wamid="wamid.N", button_id="optin_no", timestamp=TS + 20))
        await rig.run(thread_id)
    assert thread_of(world).opted_in_at is None
    assert "only reply when you write to us" in rig.texts()[-1]
    assert rig.default_llm.calls == []  # a button tap is free


# ------------------------------------------------------------------ the follow-up


def opted_in(thread: ChannelThread, *, asked_hours_ago: float, **fields) -> ChannelThread:
    thread.opt_in_asked_at = NOW - timedelta(hours=asked_hours_ago)
    thread.opted_in_at = NOW - timedelta(hours=asked_hours_ago)
    for name, value in fields.items():
        setattr(thread, name, value)
    return thread


async def due(world, *, hours_later: float = 0.0, **setup):
    with rig_for(world) as rig:
        thread = world.thread()
        opted_in(thread, asked_hours_ago=setup.pop("asked_hours_ago", 30), **setup)
        rig.now = NOW + timedelta(hours=hours_later)
        deps = rig.deps()
        counts = await followups.run_follow_ups(deps, rig.now)
    return rig, thread, counts


async def test_a_yes_and_no_owner_reply_gets_one_template_after_the_delay(world) -> None:
    world.connection.settings = ON
    rig, thread, counts = await due(world)
    assert counts == {"sent": 1, "skipped": 0, "failed": 0}
    (sent,) = templates(rig)
    assert sent.json["template"] == {"name": "callback_followup", "language": {"code": "en"}}
    assert sent.json["to"] == "919822012345"
    assert thread.follow_up_sent_at is not None


async def test_not_before_the_delay(world) -> None:
    world.connection.settings = ON
    rig, thread, counts = await due(world, asked_hours_ago=5)
    assert templates(rig) == [] and counts["sent"] == 0 and thread.follow_up_sent_at is None


async def test_never_twice(world) -> None:
    world.connection.settings = ON
    rig, _thread, _ = await due(world)
    again = await followups.run_follow_ups(rig.deps(), rig.now)
    assert len(templates(rig)) == 1 and again == {"sent": 0, "skipped": 0, "failed": 0}


@pytest.mark.parametrize(
    "setup",
    [
        {"opted_in_at": None},  # never said yes
        {"opted_out_at": NOW - timedelta(hours=1)},  # said STOP
        {"follow_up_sent_at": NOW - timedelta(hours=2)},  # already sent
    ],
)
async def test_nothing_goes_to_someone_who_has_not_said_yes_or_said_stop(world, setup) -> None:
    world.connection.settings = ON
    rig, _thread, _counts = await due(world, **setup)
    assert templates(rig) == []


async def test_not_when_follow_ups_are_off_or_incomplete(world) -> None:
    for settings in (
        {},
        {"follow_up": {"enabled": False, "template_name": "x", "language": "en"}},
        {"follow_up": {"enabled": True, "template_name": None, "language": "en"}},
    ):
        world2 = WhatsAppWorld()
        world2.connection.settings = settings
        rig, _t, _c = await due(world2)
        assert templates(rig) == []


async def test_not_when_the_owner_has_replied_since(world) -> None:
    world.connection.settings = ON
    with rig_for(world) as rig:
        thread = opted_in(world.thread(), asked_hours_ago=30)
        conv = world.db.add(
            Conversation(corpus_id=world.corpus.id, channel="whatsapp", kind="assistant")
        )
        thread.conversation_id = conv.id
        world.db.add(
            Message(
                conversation_id=conv.id,
                role="staff",
                content="Called you just now.",
                created_at=NOW - timedelta(hours=3),
            )
        )
        counts = await followups.run_follow_ups(rig.deps(), NOW)
    assert templates(rig) == [] and counts["skipped"] == 1


async def test_stop_blocks_a_follow_up_that_was_due(world) -> None:
    world.connection.settings = ON
    with rig_for(world) as rig:
        thread_id = await ask_for_a_person(rig, world)
        await rig.deliver(inbound_button(wamid="wamid.Y", button_id="optin_yes", timestamp=TS + 10))
        await rig.run(thread_id)
        await rig.deliver(inbound_text(wamid="wamid.S", text="STOP", timestamp=TS + 20))
        await rig.run(thread_id)
        sent_before = len(rig.sends())
        counts = await followups.run_follow_ups(rig.deps(), NOW + timedelta(hours=30))
    assert len(rig.sends()) == sent_before and counts["sent"] == 0  # nothing business-initiated


async def test_a_template_meta_refuses_is_not_retried(world) -> None:
    from tests.unit.whatsapp_fixtures import FakeGraph

    world.connection.settings = ON
    graph = FakeGraph(
        status=400, reply={"error": {"code": 132001, "message": "Template does not exist"}}
    )
    with rig_for(world, graph=graph) as rig:
        thread = opted_in(world.thread(), asked_hours_ago=30)
        first = await followups.run_follow_ups(rig.deps(), NOW)
        second = await followups.run_follow_ups(rig.deps(), NOW)
    assert first["failed"] == 1 and second == {"sent": 0, "skipped": 0, "failed": 0}
    assert thread.follow_up_sent_at is not None


async def test_a_plan_that_lost_whatsapp_sends_nothing(world) -> None:
    world.connection.settings = ON
    with rig_for(world) as rig:
        opted_in(world.thread(), asked_hours_ago=30)
        world.db.default_plan.channels = {}
        counts = await followups.run_follow_ups(rig.deps(), NOW)
    assert templates(rig) == [] and counts["sent"] == 0


def test_follow_up_settings_need_a_template_and_a_language() -> None:
    from types import SimpleNamespace

    assert followups.follow_up_settings(SimpleNamespace(settings=ON)) == {
        "template_name": "callback_followup",
        "language": "en",
        "after_hours": 24,
    }
    assert followups.follow_up_settings(SimpleNamespace(settings={})) is None
    odd = {
        "follow_up": {"enabled": True, "template_name": "t", "language": "en", "after_hours": 999}
    }
    assert followups.follow_up_settings(SimpleNamespace(settings=odd))["after_hours"] == 24
