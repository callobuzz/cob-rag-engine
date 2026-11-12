"""A throttled WhatsApp send is retried without running the model again: the written reply is kept
on the event, the retry sends only the messages that have not gone out."""

from datetime import timedelta

import pytest

from callobuzz_rag.channels.whatsapp.inbound import MAX_ATTEMPTS
from callobuzz_rag.db.models.channel import ChannelThread
from tests.unit.conversation_helpers import FakeLLM
from tests.unit.whatsapp_fixtures import FakeGraph, inbound_text
from tests.unit.whatsapp_rig import BUSINESS, rig_for
from tests.unit.whatsapp_world import WhatsAppWorld

pytestmark = pytest.mark.assistant_on

OK = (200, {"messages": [{"id": "wamid.OUT"}]})
THROTTLE = (429, {"error": {"code": 130429, "message": "rate"}})
READ = (200, {"success": True})
# Three paragraphs of about 2000 characters: one WhatsApp message each at most, so several parts.
LONG = [
    f"Section {n}: Home collection costs Rs 150 [1]. " + "It is quick and clean. " * 90 + "\n\n"
    for n in range(3)
]


@pytest.fixture
def world() -> WhatsAppWorld:
    return WhatsAppWorld()


def long_llm() -> FakeLLM:
    return FakeLLM(json_replies=[BUSINESS], stream=LONG)


def state(world, wamid):
    (event,) = world.events(wamid)
    return event


async def test_a_throttled_send_does_not_run_the_model_again(world) -> None:
    graph = FakeGraph(script=[READ, THROTTLE, OK, OK, OK])
    with rig_for(world, graph=graph) as rig:
        rig.default_llm = long_llm()
        spare = FakeLLM(json_replies=[BUSINESS], stream=["SECOND RUN"])
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.P1"))
        await rig.run(thread_id)
        event = state(world, "wamid.P1")
        assert event.status == "received" and event.pending_reply_encrypted
        assert len(rig.sends()) == 1  # the throttled attempt
        rig.llms.append(spare)
        rig.now = event.retry_at + timedelta(seconds=1)
        counts = await rig.run(thread_id)
    assert counts["done"] == 1 and state(world, "wamid.P1").status == "done"
    assert spare.calls == [] and len(rig.metered()) == 1
    assert len(rig.stored("assistant")) == 1
    bodies = rig.texts()[1:]
    assert len(bodies) >= 2 and all("SECOND RUN" not in b for b in bodies)
    assert state(world, "wamid.P1").pending_reply_encrypted is None


async def test_a_partly_sent_reply_resumes_with_the_unsent_part(world) -> None:
    graph = FakeGraph(script=[READ, OK, THROTTLE, OK, OK, OK])
    with rig_for(world, graph=graph) as rig:
        rig.default_llm = long_llm()
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.P2"))
        await rig.run(thread_id)
        first = rig.texts()
        assert len(first) == 2  # part one went out, part two was throttled
        event = state(world, "wamid.P2")
        rig.now = event.retry_at + timedelta(seconds=1)
        await rig.run(thread_id)
    bodies = rig.texts()
    assert bodies[2] == first[1] and bodies[0] != bodies[1]  # the throttled one, once more
    assert bodies.count(first[0]) == 1  # part one never resent
    assert len(rig.metered()) == 1 and len(rig.stored("assistant")) == 1


async def test_a_retry_after_the_window_closed_sends_nothing(world) -> None:
    graph = FakeGraph(script=[READ, THROTTLE])
    with rig_for(world, graph=graph) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.P3"))
        await rig.run(thread_id)
        sent = len(rig.sends())
        rig.now = rig.now + timedelta(hours=25)
        await rig.run(thread_id)
    event = state(world, "wamid.P3")
    assert (event.status, event.error_code) == ("failed", "window_closed")
    assert len(rig.sends()) == sent and event.pending_reply_encrypted is None
    assert world.db.all(ChannelThread)[0].unread_count == 1


async def test_giving_up_clears_the_pending_reply(world) -> None:
    graph = FakeGraph(script=[READ, THROTTLE])
    with rig_for(world, graph=graph) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.P4"))
        await rig.run(thread_id)
        event = state(world, "wamid.P4")
        assert event.pending_reply_encrypted
        event.attempts = MAX_ATTEMPTS - 1
        rig.graph.script.append(THROTTLE)
        rig.now = event.retry_at + timedelta(seconds=1)
        await rig.run(thread_id)
    event = state(world, "wamid.P4")
    assert (event.status, event.error_code) == ("failed", "rate_limited")
    assert event.pending_reply_encrypted is None and len(rig.metered()) == 1
