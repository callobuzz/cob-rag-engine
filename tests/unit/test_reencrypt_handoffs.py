"""Key rotation reaches a handoff's stored summary (conversation C5)."""

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from cryptography.fernet import Fernet

from callobuzz_rag.core import reencrypt
from callobuzz_rag.core.secrets import FernetEncryptor

OLD = Fernet.generate_key().decode()
NEW = Fernet.generate_key().decode()


def test_handoff_rotator_is_registered() -> None:
    assert any(r.__name__ == "_rotate_handoffs" for r in reencrypt._ROTATORS)


def _factory(handoff: Any) -> Any:
    ids = MagicMock()
    ids.scalars.return_value.all.return_value = [handoff.id]
    session = MagicMock()
    session.execute = AsyncMock(return_value=ids)
    session.get = AsyncMock(return_value=handoff)
    session.commit = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return factory, session


async def test_rotation_rewrites_the_summary_and_a_dry_run_changes_nothing() -> None:
    handoff = SimpleNamespace(
        id=uuid.uuid4(), summary_encrypted=FernetEncryptor(OLD).encrypt("Wants a refund.")
    )
    factory, session = _factory(handoff)
    rotating = FernetEncryptor(f"{NEW},{OLD}")
    before = handoff.summary_encrypted

    dry = reencrypt.RotationSummary()
    await reencrypt._rotate_handoffs(factory, rotating, False, dry)
    assert dry.rotated == 1 and handoff.summary_encrypted == before
    session.commit.assert_not_awaited()

    done = reencrypt.RotationSummary()
    await reencrypt._rotate_handoffs(factory, rotating, True, done)
    assert (done.scanned, done.rotated, done.failed) == (1, 1, [])
    assert FernetEncryptor(NEW).decrypt(handoff.summary_encrypted) == "Wants a refund."
    session.commit.assert_awaited_once()


async def test_a_foreign_token_is_a_failure_not_a_crash() -> None:
    other = FernetEncryptor(Fernet.generate_key().decode())
    handoff = SimpleNamespace(id=uuid.uuid4(), summary_encrypted=other.encrypt("x"))
    factory, session = _factory(handoff)
    summary = reencrypt.RotationSummary()
    await reencrypt._rotate_handoffs(factory, FernetEncryptor(f"{NEW},{OLD}"), True, summary)
    assert summary.rotated == 0 and len(summary.failed) == 1
    session.commit.assert_not_awaited()
