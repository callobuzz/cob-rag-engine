"""Key rotation reaches WhatsApp ciphertext: tokens, PINs, contacts and stored payloads (TV6)."""

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from cryptography.fernet import Fernet

from callobuzz_rag.conversation.leads.crypto import LeadCrypto
from callobuzz_rag.core import reencrypt
from callobuzz_rag.core.secrets import FernetEncryptor

OLD = Fernet.generate_key().decode()
NEW = Fernet.generate_key().decode()


def _factory(row: Any) -> tuple[Any, Any]:
    ids = MagicMock()
    ids.scalars.return_value.all.return_value = [row.id]
    session = MagicMock()
    session.execute = AsyncMock(return_value=ids)
    session.get = AsyncMock(return_value=row)
    session.commit = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return factory, session


def test_the_three_rotators_are_registered() -> None:
    names = {r.__name__ for r in reencrypt._ROTATORS}
    assert {
        "_rotate_channel_connections",
        "_rotate_channel_threads",
        "_rotate_channel_events",
    } <= names


async def test_a_connection_token_and_pin_are_rewritten() -> None:
    old = FernetEncryptor(OLD)
    row = SimpleNamespace(
        id=uuid.uuid4(),
        access_token_encrypted=old.encrypt("EAAG-token"),
        pin_encrypted=old.encrypt("123456"),
    )
    factory, session = _factory(row)
    rotating = FernetEncryptor(f"{NEW},{OLD}")
    before = row.access_token_encrypted

    dry = reencrypt.RotationSummary()
    await reencrypt._rotate_channel_connections(factory, rotating, False, dry)
    assert dry.rotated == 1 and row.access_token_encrypted == before
    session.commit.assert_not_awaited()

    done = reencrypt.RotationSummary()
    await reencrypt._rotate_channel_connections(factory, rotating, True, done)
    assert (done.scanned, done.rotated, done.failed) == (1, 1, [])
    new = FernetEncryptor(NEW)
    assert new.decrypt(row.access_token_encrypted) == "EAAG-token"
    assert new.decrypt(row.pin_encrypted) == "123456"


async def test_a_connection_without_a_pin_is_fine() -> None:
    row = SimpleNamespace(
        id=uuid.uuid4(),
        access_token_encrypted=FernetEncryptor(OLD).encrypt("tok"),
        pin_encrypted=None,
    )
    factory, _ = _factory(row)
    summary = reencrypt.RotationSummary()
    await reencrypt._rotate_channel_connections(
        factory, FernetEncryptor(f"{NEW},{OLD}"), True, summary
    )
    assert summary.rotated == 1 and row.pin_encrypted is None


async def test_a_thread_rotates_its_contact_and_recomputes_the_search_hash() -> None:
    old = FernetEncryptor(OLD)
    row = SimpleNamespace(
        id=uuid.uuid4(),
        contact_encrypted=old.encrypt("+919822012345"),
        profile_name_encrypted=old.encrypt("Asha"),
        contact_hash=LeadCrypto(old).index_hash("+919822012345"),
    )
    factory, _ = _factory(row)
    summary = reencrypt.RotationSummary()
    await reencrypt._rotate_channel_threads(factory, FernetEncryptor(f"{NEW},{OLD}"), True, summary)
    new = FernetEncryptor(NEW)
    assert new.decrypt(row.contact_encrypted) == "+919822012345"
    assert new.decrypt(row.profile_name_encrypted) == "Asha"
    assert row.contact_hash == LeadCrypto(new).index_hash("+919822012345")


async def test_an_event_payload_is_rewritten_and_a_foreign_token_is_a_failure() -> None:
    row = SimpleNamespace(
        id=uuid.uuid4(),
        payload_encrypted=FernetEncryptor(OLD).encrypt("{}"),
        pending_reply_encrypted=None,
    )
    factory, _ = _factory(row)
    ok = reencrypt.RotationSummary()
    await reencrypt._rotate_channel_events(factory, FernetEncryptor(f"{NEW},{OLD}"), True, ok)
    assert FernetEncryptor(NEW).decrypt(row.payload_encrypted) == "{}"

    foreign = SimpleNamespace(
        id=uuid.uuid4(),
        payload_encrypted=FernetEncryptor(Fernet.generate_key().decode()).encrypt("x"),
        pending_reply_encrypted=None,
    )
    factory, session = _factory(foreign)
    bad = reencrypt.RotationSummary()
    await reencrypt._rotate_channel_events(factory, FernetEncryptor(f"{NEW},{OLD}"), True, bad)
    assert bad.rotated == 0 and len(bad.failed) == 1
    session.commit.assert_not_awaited()


async def test_a_kept_reply_is_rewritten_with_the_event_payload() -> None:
    old = FernetEncryptor(OLD)
    row = SimpleNamespace(
        id=uuid.uuid4(),
        payload_encrypted=old.encrypt("{}"),
        pending_reply_encrypted=old.encrypt('{"parts": []}'),
    )
    factory, _ = _factory(row)
    ok = reencrypt.RotationSummary()
    await reencrypt._rotate_channel_events(factory, FernetEncryptor(f"{NEW},{OLD}"), True, ok)
    assert FernetEncryptor(NEW).decrypt(row.pending_reply_encrypted) == '{"parts": []}'
