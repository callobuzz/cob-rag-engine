"""Key rotation and chats (TV6 review): one chat per customer across a key change."""

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from callobuzz_rag.channels.whatsapp.store import get_or_create_thread
from callobuzz_rag.conversation.leads.crypto import LeadCrypto
from callobuzz_rag.core import reencrypt
from callobuzz_rag.core.secrets import FernetEncryptor
from callobuzz_rag.db.models.channel import ChannelEvent, ChannelThread
from callobuzz_rag.db.models.conversation import Conversation
from tests.unit.whatsapp_world import CUSTOMER, KEY, WhatsAppWorld

NEW = Fernet.generate_key().decode()
NOW = datetime(2026, 10, 5, tzinfo=UTC)


@pytest.fixture
def world() -> WhatsAppWorld:
    return WhatsAppWorld()


def rotated_crypto() -> LeadCrypto:
    """The engine after rotation began: NEW is primary, KEY still decrypts."""
    return LeadCrypto(FernetEncryptor(f"{NEW},{KEY}"))


async def test_a_chat_written_under_the_old_key_is_found_under_the_new_one(world) -> None:
    old = world.thread()  # hashed under KEY
    session = world.db.session()
    found = await get_or_create_thread(
        session, rotated_crypto(), world.connection, CUSTOMER, profile_name=None, now=NOW
    )
    assert found is old and len(world.db.all(ChannelThread)) == 1


async def test_rotation_merges_a_chat_that_was_duplicated_after_the_key_changed(world) -> None:
    older = world.thread(
        opted_out_at=NOW, unread_count=2, created_at=NOW - timedelta(days=3)
    )  # old key
    crypto = rotated_crypto()
    newer = world.db.add(
        ChannelThread(
            connection_id=world.connection.id,
            corpus_id=world.corpus.id,
            tenant_id=world.shree.id,
            contact_hash=crypto.index_hash("+" + CUSTOMER),
            contact_encrypted=crypto.encryptor.encrypt("+" + CUSTOMER),
            unread_count=1,
            created_at=NOW - timedelta(days=1),
        )
    )
    conversation = world.db.add(
        Conversation(corpus_id=world.corpus.id, channel="whatsapp", channel_thread_id=newer.id)
    )
    event = world.db.add(
        ChannelEvent(
            connection_id=world.connection.id,
            corpus_id=world.corpus.id,
            external_id="wamid.M1",
            kind="message",
            thread_id=newer.id,
            payload_encrypted="x",
            status="done",
        )
    )

    summary = reencrypt.RotationSummary()
    await reencrypt._rotate_channel_threads(world.db.factory(), crypto.encryptor, True, summary)

    assert summary.failed == []
    (thread,) = world.db.all(ChannelThread)
    assert thread is older and thread.contact_hash == crypto.index_hash("+" + CUSTOMER)
    assert thread.opted_out_at == NOW  # an opt-out is never lost
    assert thread.unread_count == 3
    assert event.thread_id == older.id and conversation.channel_thread_id == older.id
