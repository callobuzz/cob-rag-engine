"""Key rotation reaches lead contact data and its keyed search hashes (conversation C4)."""

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


def test_lead_rotator_is_registered() -> None:
    assert any(r.__name__ == "_rotate_leads" for r in reencrypt._ROTATORS)


def _factory(lead: Any) -> Any:
    ids = MagicMock()
    ids.scalars.return_value.all.return_value = [lead.id]
    session = MagicMock()
    session.execute = AsyncMock(return_value=ids)
    session.get = AsyncMock(return_value=lead)
    session.commit = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return factory, session


async def test_rotation_rewrites_tokens_and_recomputes_hashes() -> None:
    old_crypto = LeadCrypto(FernetEncryptor(OLD))
    lead = SimpleNamespace(
        id=uuid.uuid4(),
        fields_encrypted=old_crypto.encrypt_json(
            {"name": "Priya", "phone": "+919876543210", "email": "Priya@x.in", "custom": {}}
        ),
        notes_encrypted=old_crypto.encrypt_text("call at 6"),
        summary_encrypted=old_crypto.encrypt_text("wants a visit"),
        phone_hash=old_crypto.index_hash("+919876543210"),
        email_hash=old_crypto.index_hash("priya@x.in"),
    )
    factory, session = _factory(lead)
    rotating = FernetEncryptor(f"{NEW},{OLD}")
    summary = reencrypt.RotationSummary()

    await reencrypt._rotate_leads(factory, rotating, True, summary)

    assert (summary.scanned, summary.rotated, summary.failed) == (1, 1, [])
    only_new = LeadCrypto(FernetEncryptor(NEW))  # the old key is gone after step 4
    assert only_new.decrypt_json(lead.fields_encrypted)["phone"] == "+919876543210"
    assert only_new.decrypt_text(lead.notes_encrypted) == "call at 6"
    assert only_new.decrypt_text(lead.summary_encrypted) == "wants a visit"
    assert lead.phone_hash == only_new.index_hash("+919876543210")
    assert lead.email_hash == only_new.index_hash("priya@x.in")
    assert lead.phone_hash != old_crypto.index_hash("+919876543210")
    session.commit.assert_awaited_once()


async def test_dry_run_changes_nothing_and_a_foreign_token_is_a_failure() -> None:
    other = LeadCrypto(FernetEncryptor(Fernet.generate_key().decode()))
    lead = SimpleNamespace(
        id=uuid.uuid4(),
        fields_encrypted=other.encrypt_json({"phone": "+919876543210"}),
        notes_encrypted=None,
        summary_encrypted=None,
        phone_hash="keep",
        email_hash=None,
    )
    factory, session = _factory(lead)
    summary = reencrypt.RotationSummary()
    await reencrypt._rotate_leads(factory, FernetEncryptor(f"{NEW},{OLD}"), True, summary)
    assert summary.rotated == 0 and len(summary.failed) == 1
    assert lead.phone_hash == "keep"
    session.commit.assert_not_awaited()

    mine = LeadCrypto(FernetEncryptor(OLD))
    lead.fields_encrypted = mine.encrypt_json({"phone": "+919876543210"})
    before = lead.fields_encrypted
    summary = reencrypt.RotationSummary()
    await reencrypt._rotate_leads(factory, FernetEncryptor(f"{NEW},{OLD}"), False, summary)
    assert summary.rotated == 1 and lead.fields_encrypted == before
    session.commit.assert_not_awaited()
