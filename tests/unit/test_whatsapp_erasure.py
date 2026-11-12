"""DPDP: erasing a lead erases the customer's WhatsApp chat with it (TV6 review)."""

from datetime import UTC, datetime

import pytest

from callobuzz_rag.channels.whatsapp.erasure import erase_chats
from callobuzz_rag.db.models.channel import ChannelEvent, ChannelThread
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.handoff import Handoff
from callobuzz_rag.db.models.message import Message
from tests.unit.whatsapp_world import CUSTOMER, WhatsAppWorld

NOW = datetime(2026, 10, 5, tzinfo=UTC)


@pytest.fixture
def world() -> WhatsAppWorld:
    return WhatsAppWorld()


def conversation(world, thread, *, channel="whatsapp", current=False) -> Conversation:
    conv = world.db.add(
        Conversation(
            corpus_id=world.corpus.id,
            channel=channel,
            channel_thread_id=thread.id if channel == "whatsapp" else None,
        )
    )
    world.db.add(Message(conversation_id=conv.id, role="user", content="my number is 98220"))
    if current:
        thread.conversation_id = conv.id
    return conv


def event(world, thread, wamid) -> ChannelEvent:
    return world.db.add(
        ChannelEvent(
            connection_id=world.connection.id,
            corpus_id=world.corpus.id,
            external_id=wamid,
            kind="message",
            thread_id=thread.id,
            payload_encrypted="x",
            status="done",
        )
    )


async def test_the_whole_chat_goes_not_only_the_leads_conversation(world) -> None:
    thread = world.thread()
    other = world.thread(contact="919800099999")
    old = conversation(world, thread)  # an earlier 24-hour window
    current = conversation(world, thread, current=True)
    keep = conversation(world, other, current=True)
    event(world, thread, "wamid.1")
    kept_event = event(world, other, "wamid.2")
    handoff = world.db.add(
        Handoff(
            corpus_id=world.corpus.id,
            tenant_id=world.shree.id,
            conversation_id=old.id,
            channel="whatsapp_chat",
            reason="explicit_request",
            summary_encrypted="sealed",
        )
    )

    deleted = await erase_chats(world.db.session(), [(world.corpus.id, current.id, None)])

    assert deleted == 1
    assert world.db.all(ChannelThread) == [other]
    assert world.db.all(Conversation) == [keep]
    assert [m.conversation_id for m in world.db.all(Message)] == [keep.id]
    assert world.db.all(ChannelEvent) == [kept_event]
    assert handoff.summary_encrypted is None  # the row stays as a count, the summary does not


async def test_a_lead_from_the_website_erases_the_same_persons_chat_by_phone(world) -> None:
    thread = world.thread()
    conversation(world, thread, current=True)
    widget = conversation(world, thread, channel="widget")
    other_corpus = world.db.add(
        Corpus(name="Other", tenant_id=world.shree.id, qdrant_collection="c2", api_key_hash="")
    )
    elsewhere = world.db.add(
        ChannelThread(
            connection_id=world.connection.id,
            corpus_id=other_corpus.id,
            tenant_id=world.shree.id,
            contact_hash=thread.contact_hash,
            contact_encrypted="x",
        )
    )

    deleted = await erase_chats(
        world.db.session(), [(world.corpus.id, widget.id, thread.contact_hash)]
    )

    assert deleted == 1
    assert world.db.all(ChannelThread) == [elsewhere]  # another knowledge base is not touched
    assert widget in world.db.all(Conversation)  # the website chat is the lead code's to delete
    assert len(world.db.all(Conversation)) == 1


async def test_a_chat_that_predates_the_link_is_found_by_its_current_conversation(world) -> None:
    thread = world.thread()
    old_style = world.db.add(Conversation(corpus_id=world.corpus.id, channel="whatsapp"))
    thread.conversation_id = old_style.id  # no `channel_thread_id` on it
    await erase_chats(world.db.session(), [(world.corpus.id, old_style.id, None)])
    assert world.db.all(ChannelThread) == [] and world.db.all(Conversation) == []


async def test_nothing_to_find_means_nothing_is_touched(world) -> None:
    thread = world.thread(contact=CUSTOMER)
    assert await erase_chats(world.db.session(), [(world.corpus.id, None, None)]) == 0
    assert await erase_chats(world.db.session(), []) == 0
    assert world.db.all(ChannelThread) == [thread]


async def test_every_window_of_a_chat_is_linked_to_the_chat(world) -> None:
    from datetime import timedelta

    from tests.unit.whatsapp_fixtures import inbound_text
    from tests.unit.whatsapp_rig import NOW as RIG_NOW
    from tests.unit.whatsapp_rig import rig_for

    with rig_for(world) as rig:
        (thread_id,) = await rig.deliver(inbound_text(wamid="wamid.W1", text="First?"))
        await rig.run(thread_id)
        rig.now = RIG_NOW + timedelta(hours=30)
        late = int(rig.now.timestamp()) - 5
        await rig.deliver(inbound_text(wamid="wamid.W2", text="Second?", timestamp=late))
        await rig.run(thread_id)
    conversations = world.db.all(Conversation)
    assert len(conversations) == 2
    assert {c.channel_thread_id for c in conversations} == {thread_id}
