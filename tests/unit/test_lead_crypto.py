"""Lead contact data is encrypted with the engine's Fernet key; index hashes allow exact search."""

import time

import pytest
from cryptography.fernet import Fernet

from callobuzz_rag.conversation.leads.crypto import (
    LeadCrypto,
    LeadCryptoUnavailableError,
    lead_crypto_from_settings,
)
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.secrets import EncryptionError, FernetEncryptor

KEY = Fernet.generate_key().decode()
OLD = Fernet.generate_key().decode()


def test_settings_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.lead_rate_limit_per_hour == 5
    assert s.lead_retention_schedule_cron == "30 3 * * *"
    assert s.lead_export_max_rows == 50000


def test_primary_key_property() -> None:
    assert FernetEncryptor(f"{KEY},{OLD}").primary_key == KEY


def test_decrypt_fresh_accepts_young_and_refuses_old_tokens() -> None:
    enc = FernetEncryptor(KEY)
    token = enc.encrypt("hello")
    assert enc.decrypt_fresh(token, ttl_s=60) == "hello"
    old = Fernet(KEY.encode()).encrypt_at_time(b"hello", int(time.time()) - 120).decode()
    with pytest.raises(EncryptionError):
        enc.decrypt_fresh(old, ttl_s=60)
    with pytest.raises(EncryptionError):
        enc.decrypt_fresh("not-a-token", ttl_s=60)


def test_round_trip_and_no_plaintext() -> None:
    crypto = lead_crypto_from_settings(Settings(_env_file=None, encryption_key=KEY))
    token = crypto.encrypt_json({"name": "Priya Kulkarni", "phone": "+919876543210"})
    assert "Priya" not in token and "9876543210" not in token
    assert crypto.decrypt_json(token) == {"name": "Priya Kulkarni", "phone": "+919876543210"}
    assert crypto.encrypt_text(None) is None
    assert crypto.decrypt_text(crypto.encrypt_text("call after 6 pm")) == "call after 6 pm"


def test_index_hash_is_stable_keyed_and_none_safe() -> None:
    a = LeadCrypto(FernetEncryptor(KEY))
    b = LeadCrypto(FernetEncryptor(f"{KEY},{OLD}"))  # same primary key -> same hash
    c = LeadCrypto(FernetEncryptor(OLD))
    assert a.index_hash("+919876543210") == b.index_hash("+919876543210")
    assert a.index_hash("+919876543210") != c.index_hash("+919876543210")
    assert len(a.index_hash("+919876543210") or "") == 64
    assert a.index_hash(None) is None


def test_index_hashes_cover_every_configured_key_so_search_survives_rotation() -> None:
    old_crypto = LeadCrypto(FernetEncryptor(OLD))
    rotating = LeadCrypto(FernetEncryptor(f"{KEY},{OLD}"))
    phone = "+919876543210"
    hashes = rotating.index_hashes(phone)
    assert len(hashes) == 2
    assert hashes[0] == rotating.index_hash(phone)
    assert old_crypto.index_hash(phone) in hashes
    assert rotating.index_hashes(None) == []


def test_no_key_means_unavailable() -> None:
    with pytest.raises(LeadCryptoUnavailableError):
        lead_crypto_from_settings(Settings(_env_file=None, encryption_key=""))
