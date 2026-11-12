"""Key rotation reaches the stored payment keys and webhook payloads (P1)."""

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from cryptography.fernet import Fernet

from callobuzz_rag.core import reencrypt
from callobuzz_rag.core.secrets import FernetEncryptor

OLD = Fernet.generate_key().decode()
NEW = Fernet.generate_key().decode()


def test_payment_rotators_are_registered() -> None:
    names = {r.__name__ for r in reencrypt._ROTATORS}
    assert {"_rotate_payment_settings", "_rotate_payment_events"} <= names


def _factory(row: Any) -> Any:
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


async def test_both_secrets_move_to_the_new_key() -> None:
    old = FernetEncryptor(OLD)
    row = SimpleNamespace(
        id=1,
        key_secret_encrypted=old.encrypt("the-key-secret"),
        webhook_secret_encrypted=old.encrypt("the-webhook-secret"),
    )
    factory, session = _factory(row)
    summary = reencrypt.RotationSummary()
    await reencrypt._rotate_payment_settings(
        factory, FernetEncryptor(f"{NEW},{OLD}"), True, summary
    )
    assert (summary.rotated, summary.failed) == (1, [])
    new = FernetEncryptor(NEW)
    assert new.decrypt(row.key_secret_encrypted) == "the-key-secret"
    assert new.decrypt(row.webhook_secret_encrypted) == "the-webhook-secret"
    session.commit.assert_awaited_once()


async def test_an_event_payload_moves_to_the_new_key_and_a_dry_run_changes_nothing() -> None:
    row = SimpleNamespace(id=uuid.uuid4(), payload_encrypted=FernetEncryptor(OLD).encrypt("{}"))
    factory, session = _factory(row)
    before = row.payload_encrypted
    dry = reencrypt.RotationSummary()
    await reencrypt._rotate_payment_events(factory, FernetEncryptor(f"{NEW},{OLD}"), False, dry)
    assert dry.rotated == 1 and row.payload_encrypted == before
    session.commit.assert_not_awaited()
    done = reencrypt.RotationSummary()
    await reencrypt._rotate_payment_events(factory, FernetEncryptor(f"{NEW},{OLD}"), True, done)
    assert FernetEncryptor(NEW).decrypt(row.payload_encrypted) == "{}"
