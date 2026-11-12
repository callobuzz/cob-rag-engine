"""TV6 review fixes on the answering side: STOP, tenant status, dead tokens, retries."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.channels.whatsapp.inbound import MAX_ATTEMPTS
from callobuzz_rag.conversation.store import RuntimeConfig
from callobuzz_rag.db.models.channel import ChannelThread
from callobuzz_rag.worker import whatsapp_tasks
from tests.unit.conversation_helpers import FakeLLM
from tests.unit.whatsapp_fixtures import (
    FakeGraph,
    inbound_button,
    inbound_image,
    inbound_text,
)
from tests.unit.whatsapp_rig import BUSINESS, NOW, rig_for
from tests.unit.whatsapp_world import WhatsAppWorld

pytestmark = pytest.mark.assistant_on

NUMBER = "+91 98000 00000"
THROTTLE = (429, {"error": {"code": 130429, "message": "rate"}})


@pytest.fixture
def world() -> WhatsAppWorld:
    return WhatsAppWorld()


def mail_recorder():
    sent: list[tuple[str, str, dict]] = []

    def enqueue(template, to, context, *, expires_s):
        sent.append((template, to, dict(context)))

    return sent, enqueue


def add_owner(world: WhatsAppWorld, email: str = "owner@shree.example") -> None:
    world.member(world.user(email), world.shree)


def event_state(world, wamid):
    (event,) = world.events(wamid)
    return event.status, event.error_code


# ----------------------------------------------------------- 3: STOP while paused


async def test_stop_is_honoured_while_the_bot_is_paused(world) -> None:
    world.thread(bot_paused_until=NOW + timedelta(hours=5), paused_by="echo")
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.S1", text="STOP"))
        await rig.run(thread_id)
    (thread,) = world.db.all(ChannelThread)
    assert thread.opted_out_at == NOW
    assert event_state(world, "wamid.S1") == ("done", None)


# ----------------------------------------------------------- 5: only an active workspace answers


async def test_a_suspended_workspace_is_not_stored_or_answered(world) -> None:
    world.shree.status = "suspended"
    with rig_for(world) as rig:
        woken = await rig.deliver(inbound_text(wamid="wamid.X1"))
    assert woken == [] and world.events("wamid.X1") == []


async def test_an_event_stored_before_the_suspension_is_not_answered(world) -> None:
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.X2"))
        world.shree.status = "suspended"
        await rig.run(thread_id)
    assert rig.sends() == [] and rig.default_llm.calls == []
    assert event_state(world, "wamid.X2") == ("ignored", "tenant_inactive")


# ----------------------------------------------------------- 6: a dead token, a lost plan


async def test_a_dead_token_stops_answering_and_tells_the_owner_once(world) -> None:
    add_owner(world)
    sent, enqueue = mail_recorder()
    graph = FakeGraph(status=401, reply={"error": {"code": 190, "message": "Invalid OAuth token"}})
    with rig_for(world, graph=graph) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.T1"))
        deps = rig.deps()
        deps.extras["enqueue_email"] = enqueue
        await rig.run(thread_id, deps)
        assert [m[0] for m in sent] == ["whatsapp_attention"] and sent[0][2]["reason"] == "token"
        assert sent[0][1] == "owner@shree.example" and sent[0][2]["number"] == NUMBER
        metered, requests = len(rig.metered()), len(rig.graph.requests)
        await rig.deliver(inbound_text(wamid="wamid.T2", text="Second question?"))
        await rig.run(thread_id, deps)
    assert len(rig.metered()) == metered  # nothing run, nothing metered
    assert len(rig.graph.requests) == requests  # nothing sent
    assert event_state(world, "wamid.T2") == ("ignored", "token")
    assert len(sent) == 1  # one mail, not one per message
    assert [m.content for m in rig.stored("user")][-1] == "Second question?"
    assert world.db.all(ChannelThread)[0].unread_count == 1


async def test_a_plan_that_lost_whatsapp_tells_the_owner_once(world) -> None:
    add_owner(world)
    sent, enqueue = mail_recorder()
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.P1"))
        world.db.default_plan.channels = {"whatsapp": False}
        deps = rig.deps()
        deps.extras["enqueue_email"] = enqueue
        await rig.run(thread_id, deps)
        await rig.deliver(inbound_text(wamid="wamid.P2"))
        await rig.run(thread_id, deps)
    assert [(m[0], m[2]["reason"]) for m in sent] == [("whatsapp_attention", "plan")]


# ----------------------------------------------------------- 7: the assistant is off


async def test_with_the_assistant_off_a_message_is_kept_unread(world) -> None:
    with rig_for(world, assistant=False) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.O1", text="Is it open?"))
        await rig.run(thread_id)
    assert rig.sends() == []
    assert [m.content for m in rig.stored("user")] == ["Is it open?"]
    assert world.db.all(ChannelThread)[0].unread_count == 1


async def test_with_the_assistant_off_a_request_for_a_person_is_still_answered(world) -> None:
    with rig_for(world, assistant=False) as rig:
        off = RuntimeConfig(
            config=rig.config.model_copy(update={"enabled": False}),
            version=3,
            draft_revision=None,
            enabled=False,
        )
        with patch(
            "callobuzz_rag.conversation.store.get_runtime_config", AsyncMock(return_value=off)
        ):
            (thread_id,) = await rig.deliver(
                inbound_text(wamid="wamid.O2", text="Kisi insaan se baat karni hai")
            )
            await rig.run(thread_id)
    assert "SL-4F7K" in "\n".join(rig.texts())
    assert rig.default_llm.calls == [] and rig.io.meter.await_count == 0
    (call,) = rig.offered.call_args_list
    assert call.args[0]["channel"] == "whatsapp_chat"


# ----------------------------------------------------------- 13: a throttled send is tried again


async def test_a_throttled_send_is_queued_again_with_a_backoff(world) -> None:
    graph = FakeGraph(script=[(200, {"success": True}), THROTTLE])
    with rig_for(world, graph=graph) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.R1"))
        counts = await rig.run(thread_id)
        (event,) = world.events("wamid.R1")
        assert counts.get("retry") == 1 and event.status == "received"
        assert event.attempts == 1 and event.retry_at > NOW and event.claimed_at is None
        assert await rig.run(thread_id) == {"done": 0, "ignored": 0, "failed": 0}  # not yet
        rig.now = event.retry_at + timedelta(seconds=1)
        rig.llms.append(
            FakeLLM(json_replies=[BUSINESS], stream=["Home collection costs Rs 150 [1]."])
        )
        counts = await rig.run(thread_id)
    assert counts["done"] == 1 and event_state(world, "wamid.R1") == ("done", None)
    assert any("Rs 150" in t for t in rig.texts())


async def test_a_throttled_send_gives_up_after_the_attempts(world) -> None:
    graph = FakeGraph(status=429, reply={"error": {"code": 130429, "message": "rate"}})
    with rig_for(world, graph=graph) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.R2"))
        (event,) = world.events("wamid.R2")
        event.attempts = MAX_ATTEMPTS - 1
        await rig.run(thread_id)
    assert event_state(world, "wamid.R2") == ("failed", "rate_limited")


async def test_the_sweep_wakes_a_chat_when_its_retry_is_due(world, monkeypatch) -> None:
    woken: list = []
    monkeypatch.setattr("callobuzz_rag.channels.whatsapp.dispatch.queue_thread", woken.append)
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.R3"))
    (event,) = world.events("wamid.R3")
    event.retry_at = NOW + timedelta(minutes=5)
    event.received_at = NOW - timedelta(minutes=5)
    await whatsapp_tasks.sweep_with(world.db.factory(), NOW)
    assert woken == []
    await whatsapp_tasks.sweep_with(world.db.factory(), NOW + timedelta(minutes=6))
    assert woken == [thread_id]


# ----------------------------------------------------------- 17: reactions and system messages


@pytest.mark.parametrize("kind", ["reaction", "system", "unsupported", "request_welcome"])
async def test_things_that_are_not_messages_get_no_reply(world, kind) -> None:
    payload = inbound_image(wamid="wamid.Z1")
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["type"] = kind
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(payload)
        await rig.run(thread_id)
    assert rig.sends() == [] and rig.default_llm.calls == []
    assert event_state(world, "wamid.Z1") == ("ignored", "silent")


# ----------------------------------------------------------- 24: stored text, no fee on fixed replies


async def test_the_customers_opt_in_tap_is_kept(world) -> None:
    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(inbound_button(wamid="wamid.B1"))
        await rig.run(thread_id)
    assert "Yes, message me" in [m.content for m in rig.stored("user")]


async def test_a_pause_reply_carries_no_pass_through_fee(world) -> None:
    after = AsyncMock()
    with rig_for(world, pause="billing") as rig:
        rig.after_send = after
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.F1"))
        await rig.run(thread_id)
    assert rig.texts() and after.await_count == 0


async def test_a_request_for_a_person_carries_no_pass_through_fee(world) -> None:
    # TV1 D2: reaching a person costs the business nothing, not even the Meta pass-through.
    after = AsyncMock()
    with rig_for(world) as rig:
        rig.after_send = after
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.F2", text="talk to a person"))
        await rig.run(thread_id)
    assert rig.texts() and after.await_count == 0


# ----------------------------------------------------------- 8: a card that promises a person notifies


async def test_a_trigger_offered_handoff_on_whatsapp_notifies_the_owner(world) -> None:
    from tests.unit.conversation_helpers import make_config

    config = make_config(handoff={"triggers": {"keywords": ["refund"]}})
    with rig_for(world, config=config) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.K1", text="I want a refund now"))
        await rig.run(thread_id)
    assert "will reply here" in "\n".join(rig.texts())
    (call,) = rig.offered.call_args_list
    assert call.args[0]["channel"] == "whatsapp_chat" and call.args[0]["reason"] == "keyword"
