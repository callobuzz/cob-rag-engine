"""Unit tests for core/secrets.py — API key management and encryption."""

import pytest

from callobuzz_rag.core.secrets import (
    EncryptionError,
    FernetEncryptor,
    constant_time_compare,
    generate_api_key,
    generate_fernet_key,
    hash_api_key,
    parse_key_list,
    verify_api_key,
)


class TestGenerateApiKey:
    def test_generate_api_key_has_prefix(self) -> None:
        key = generate_api_key(prefix="rag")
        assert key.startswith("rag_")

    def test_generate_api_key_custom_prefix(self) -> None:
        key = generate_api_key(prefix="corpus")
        assert key.startswith("corpus_")

    def test_generate_api_key_unique(self) -> None:
        key1 = generate_api_key(prefix="rag")
        key2 = generate_api_key(prefix="rag")
        assert key1 != key2


class TestHashAndVerify:
    def test_hash_and_verify_api_key(self) -> None:
        api_key = generate_api_key(prefix="test")
        hashed = hash_api_key(api_key)
        assert verify_api_key(api_key, hashed) is True

    def test_verify_wrong_key_fails(self) -> None:
        api_key = generate_api_key(prefix="test")
        hashed = hash_api_key(api_key)
        wrong_key = generate_api_key(prefix="test")
        assert verify_api_key(wrong_key, hashed) is False

    def test_hash_is_not_plaintext(self) -> None:
        api_key = "rag_test_key_12345"
        hashed = hash_api_key(api_key)
        assert hashed != api_key
        assert hashed.startswith("$2b$")

    def test_verify_api_key_malformed_hash_returns_false(self) -> None:
        assert verify_api_key("any-key", "not-a-valid-bcrypt-hash") is False


class TestConstantTimeCompare:
    def test_constant_time_compare_equal(self) -> None:
        assert constant_time_compare("hello", "hello") is True

    def test_constant_time_compare_unequal(self) -> None:
        assert constant_time_compare("hello", "world") is False

    def test_constant_time_compare_empty(self) -> None:
        assert constant_time_compare("", "") is True

    def test_constant_time_compare_length_mismatch(self) -> None:
        assert constant_time_compare("short", "a longer string") is False


class TestFernetEncryptor:
    def test_fernet_encrypt_decrypt_roundtrip(self) -> None:
        key = generate_fernet_key()
        encryptor = FernetEncryptor(key)
        plaintext = "my-secret-api-key-12345"
        encrypted = encryptor.encrypt(plaintext)
        decrypted = encryptor.decrypt(encrypted)
        assert decrypted == plaintext

    def test_fernet_decrypt_wrong_key_fails(self) -> None:
        key1 = generate_fernet_key()
        key2 = generate_fernet_key()
        encryptor1 = FernetEncryptor(key1)
        encryptor2 = FernetEncryptor(key2)
        encrypted = encryptor1.encrypt("secret data")
        with pytest.raises(EncryptionError, match="Decryption failed"):
            encryptor2.decrypt(encrypted)

    def test_fernet_encrypted_is_not_plaintext(self) -> None:
        key = generate_fernet_key()
        encryptor = FernetEncryptor(key)
        plaintext = "visible-secret"
        encrypted = encryptor.encrypt(plaintext)
        assert encrypted != plaintext

    def test_fernet_invalid_key_raises(self) -> None:
        with pytest.raises(EncryptionError, match="Invalid Fernet key"):
            FernetEncryptor("not-a-valid-fernet-key")

    def test_fernet_empty_string_roundtrip(self) -> None:
        key = generate_fernet_key()
        encryptor = FernetEncryptor(key)
        encrypted = encryptor.encrypt("")
        assert encryptor.decrypt(encrypted) == ""

    def test_fernet_unicode_roundtrip(self) -> None:
        key = generate_fernet_key()
        encryptor = FernetEncryptor(key)
        plaintext = "Hola mundo! Shalom! Bonjour!"
        encrypted = encryptor.encrypt(plaintext)
        assert encryptor.decrypt(encrypted) == plaintext


class TestGenerateFernetKey:
    def test_generate_fernet_key_is_string(self) -> None:
        key = generate_fernet_key()
        assert isinstance(key, str)
        assert len(key) > 0

    def test_generate_fernet_key_is_valid(self) -> None:
        key = generate_fernet_key()
        encryptor = FernetEncryptor(key)
        encryptor.encrypt("test")

    def test_generate_fernet_key_unique(self) -> None:
        key1 = generate_fernet_key()
        key2 = generate_fernet_key()
        assert key1 != key2


class TestParseKeyList:
    def test_single_key_is_a_list_of_one(self) -> None:
        key = generate_fernet_key()
        assert parse_key_list(key) == [key]

    def test_comma_separated_keeps_order(self) -> None:
        a, b, c = generate_fernet_key(), generate_fernet_key(), generate_fernet_key()
        assert parse_key_list(f"{a},{b},{c}") == [a, b, c]

    def test_whitespace_and_empty_entries_are_ignored(self) -> None:
        a, b = generate_fernet_key(), generate_fernet_key()
        assert parse_key_list(f"  {a} , , {b} ,  ") == [a, b]

    def test_blank_value_yields_no_keys(self) -> None:
        assert parse_key_list("   ") == []


class TestKeyRotation:
    """The four-step rotation from FernetEncryptor's docstring, end to end.

    Real Fernet keys and real ciphertext throughout — the whole point is to
    prove that a scheduled key rotation never leaves data unreadable, and a
    mock would prove nothing about that.
    """

    def test_old_ciphertext_still_decrypts_after_adding_a_new_primary(self) -> None:
        old_key = generate_fernet_key()
        secret = "sk-or-v1-the-corpus-llm-key"
        token = FernetEncryptor(old_key).encrypt(secret)

        # Step 2: new key first, old key retained.
        new_key = generate_fernet_key()
        rotating = FernetEncryptor(f"{new_key},{old_key}")

        assert rotating.decrypt(token) == secret
        assert rotating.key_count == 2

    def test_new_writes_use_the_primary_key_only(self) -> None:
        old_key, new_key = generate_fernet_key(), generate_fernet_key()
        rotating = FernetEncryptor(f"{new_key},{old_key}")

        fresh = rotating.encrypt("written during rotation")

        # Readable by the new key alone => the old key can be destroyed once
        # every pre-existing token has been rotated.
        assert FernetEncryptor(new_key).decrypt(fresh) == "written during rotation"

    def test_rotate_rewrites_a_token_under_the_primary_key(self) -> None:
        old_key, new_key = generate_fernet_key(), generate_fernet_key()
        secret = "sk-or-v1-the-corpus-llm-key"
        token = FernetEncryptor(old_key).encrypt(secret)

        # Step 3: re-encrypt.
        rotated = FernetEncryptor(f"{new_key},{old_key}").rotate(token)

        # Step 4: the old key is gone and nothing is lost.
        assert FernetEncryptor(new_key).decrypt(rotated) == secret

    def test_rotate_is_idempotent(self) -> None:
        """A re-encrypt pass interrupted halfway can simply be re-run."""
        old_key, new_key = generate_fernet_key(), generate_fernet_key()
        enc = FernetEncryptor(f"{new_key},{old_key}")
        token = FernetEncryptor(old_key).encrypt("payload")

        once = enc.rotate(token)
        twice = enc.rotate(once)

        assert FernetEncryptor(new_key).decrypt(twice) == "payload"

    def test_dropping_a_key_too_early_fails_loudly(self) -> None:
        """Removing the old key before re-encrypting raises, never silently
        returns wrong or empty data."""
        old_key, new_key = generate_fernet_key(), generate_fernet_key()
        token = FernetEncryptor(old_key).encrypt("payload")

        with pytest.raises(EncryptionError):
            FernetEncryptor(new_key).decrypt(token)

    def test_single_key_behaviour_is_unchanged(self) -> None:
        """The historical single-key form must keep working byte for byte."""
        key = generate_fernet_key()
        enc = FernetEncryptor(key)
        assert enc.key_count == 1
        assert enc.decrypt(enc.encrypt("unchanged")) == "unchanged"

    def test_no_key_configured_raises(self) -> None:
        with pytest.raises(EncryptionError):
            FernetEncryptor("")

    def test_invalid_key_in_the_list_raises(self) -> None:
        with pytest.raises(EncryptionError):
            FernetEncryptor(f"{generate_fernet_key()},not-a-fernet-key")
