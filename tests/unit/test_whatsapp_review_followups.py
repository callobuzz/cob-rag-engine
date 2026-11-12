"""TV6 review: the follow-up queue is not clogged by chats that can never be sent."""

from datetime import timedelta

import pytest

from callobuzz_rag.channels.whatsapp import followups
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.message import Message
from tests.unit.test_whatsapp_followups import ON, opted_in, templates
from tests.unit.whatsapp_rig import NOW, rig_for
from tests.unit.whatsapp_world import WhatsAppWorld

pytestmark = pytest.mark.assistant_on


@pytest.fixture
def world() -> WhatsAppWorld:
    return WhatsAppWorld()


async def test_chats_the_owner_took_over_leave_the_queue_for_good(world, monkeypatch) -> None:
    monkeypatch.setattr(followups, "BATCH", 2)  # a small page makes the clog visible
    world.connection.settings = ON
    with rig_for(world) as rig:
        for n in range(3):  # three older chats the owner already replied to
            thread = opted_in(world.thread(contact=f"91982200000{n}"), asked_hours_ago=60 + n)
            conv = world.db.add(
                Conversation(corpus_id=world.corpus.id, channel="whatsapp", kind="assistant")
            )
            thread.conversation_id = conv.id
            world.db.add(
                Message(
                    conversation_id=conv.id,
                    role="staff",
                    content="Called you.",
                    created_at=NOW - timedelta(hours=3),
                )
            )
        waiting = opted_in(world.thread(contact="919822099999"), asked_hours_ago=30)
        first = await followups.run_follow_ups(rig.deps(), NOW)
        again = await followups.run_follow_ups(rig.deps(), NOW)
    assert first == {"sent": 1, "skipped": 3, "failed": 0}
    assert len(templates(rig)) == 1 and waiting.follow_up_sent_at is not None
    assert again == {"sent": 0, "skipped": 0, "failed": 0}  # nothing is looked at twice


async def test_a_disabled_number_does_not_crowd_out_a_live_one(world, monkeypatch) -> None:
    monkeypatch.setattr(followups, "BATCH", 1)
    dead = world.connect(phone_number_id="5550009", status="disabled", settings=ON)
    world.connection.settings = ON
    with rig_for(world) as rig:
        old = opted_in(world.thread(contact="919822000001"), asked_hours_ago=90)
        old.connection_id = dead.id
        live = opted_in(world.thread(contact="919822000002"), asked_hours_ago=30)
        counts = await followups.run_follow_ups(rig.deps(), NOW)
    assert counts["sent"] == 1 and live.follow_up_sent_at is not None
    assert old.follow_up_sent_at is None


async def test_a_suspended_workspace_sends_no_follow_up(world) -> None:
    world.connection.settings = ON
    with rig_for(world) as rig:
        thread = opted_in(world.thread(), asked_hours_ago=30)
        world.shree.status = "suspended"
        counts = await followups.run_follow_ups(rig.deps(), NOW)
    assert templates(rig) == [] and counts["sent"] == 0 and thread.follow_up_sent_at is None
