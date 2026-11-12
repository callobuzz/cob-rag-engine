"""Secrets utilities for API key management and symmetric encryption.

Provides:
- API key generation with configurable prefix
- bcrypt hashing and verification for API keys
- Constant-time string comparison
- Fernet symmetric encryption for sensitive config values
"""

import hmac
import logging
import secrets
from typing import TYPE_CHECKING

import bcrypt
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from callobuzz_rag.core.exceptions import RAGKernelError

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)


class EncryptionError(RAGKernelError):
    """Encryption or decryption operation failed."""


def generate_api_key(prefix: str = "rag") -> str:
    """Generate a cryptographically secure API key with a prefix.

    Format: {prefix}_{random_urlsafe_base64_24_bytes}
    """
    random_part = secrets.token_urlsafe(24)
    return f"{prefix}_{random_part}"


def hash_api_key(api_key: str) -> str:
    """Hash an API key using bcrypt for secure storage."""
    hashed = bcrypt.hashpw(api_key.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_api_key(api_key: str, hashed: str) -> bool:
    """Verify an API key against its bcrypt hash."""
    try:
        return bcrypt.checkpw(api_key.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def constant_time_compare(a: str, b: str) -> bool:
    """Compare two strings in constant time to prevent timing attacks."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def generate_fernet_key() -> str:
    """Generate a new Fernet encryption key."""
    return Fernet.generate_key().decode("utf-8")


def parse_key_list(raw: str) -> list[str]:
    """Split a RAG_ENCRYPTION_KEY value into individual Fernet keys.

    The value is a comma-separated list, PRIMARY FIRST:

        RAG_ENCRYPTION_KEY=<new-key>,<previous-key>

    Whitespace and empty entries are ignored so a trailing comma or a wrapped
    line in a .env file is harmless. A plain single key is just a list of one,
    so the historical single-key form keeps working unchanged.
    """
    return [part.strip() for part in raw.split(",") if part.strip()]


class FernetEncryptor:
    """Symmetric encryption using Fernet (AES-128-CBC + HMAC-SHA256).

    ROTATION. Backed by `MultiFernet`, so more than one key may be configured:
    decryption tries every key in order, encryption always uses the FIRST.
    That is what makes a scheduled key rotation a non-event instead of a data
    loss — the previous key stays available to read old ciphertext while all
    new writes already use the new one.

    A 90-day rotation is four steps with no downtime and nothing unreadable at
    any point:

        1. generate a new key
        2. RAG_ENCRYPTION_KEY=<new>,<old>   -> deploy. Old data still decrypts
                                              (old key present); new writes use
                                              <new>.
        3. run the re-encrypt command       -> every stored token is rewritten
                                              under <new>.
        4. RAG_ENCRYPTION_KEY=<new>         -> deploy. <old> is now unused and
                                              can be destroyed.

    Keyed search hashes (lead phone / e-mail) derive from the primary key; while step 2 is
    live, lookups try one hash per configured key, and step 3 rewrites them.

    Steps 2 and 4 are ordinary deploys. Step 3 is idempotent and re-runnable.

    Before this, the class wrapped a single `Fernet`, which meant the key could
    never be changed after the first write — the reason a routine credential
    rotation looked like it would destroy data.
    """

    def __init__(self, key: str) -> None:
        keys = parse_key_list(key)
        if not keys:
            raise EncryptionError(
                "No Fernet key configured. Set RAG_ENCRYPTION_KEY to one key, or to a "
                "comma-separated list with the primary key first."
            )
        try:
            fernets = [Fernet(k.encode("utf-8")) for k in keys]
        except (ValueError, TypeError) as e:
            # Deliberately does not echo the key material into the message.
            raise EncryptionError(
                f"Invalid Fernet key at position {len(keys)} or earlier: {e}"
            ) from e
        self._multi = MultiFernet(fernets)
        self._key_count = len(fernets)
        self._primary_key = keys[0]
        self._keys = tuple(keys)

    @property
    def primary_key(self) -> str:
        """The first configured key (the one new writes use). Keyed hashes derive from it."""
        return self._primary_key

    @property
    def keys(self) -> tuple[str, ...]:
        """Every configured key, primary first (needed to derive per-key search hashes)."""
        return self._keys

    @property
    def key_count(self) -> int:
        """How many keys are configured. >1 means a rotation is in progress."""
        return self._key_count

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a plaintext string using the PRIMARY (first) key."""
        token = self._multi.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt(self, token: str) -> str:
        """Decrypt a token, trying each configured key in order."""
        try:
            plaintext = self._multi.decrypt(token.encode("utf-8"))
            return plaintext.decode("utf-8")
        except InvalidToken as e:
            raise EncryptionError(
                "Decryption failed: no configured key could read this token. If a key was "
                "recently removed from RAG_ENCRYPTION_KEY, add it back as a secondary entry "
                "and run the re-encrypt command before removing it."
            ) from e

    def decrypt_fresh(self, token: str, ttl_s: int) -> str:
        """Decrypt a token that must be younger than `ttl_s` seconds (signed links)."""
        try:
            return self._multi.decrypt(token.encode("utf-8"), ttl=ttl_s).decode("utf-8")
        except InvalidToken as e:
            raise EncryptionError("Token invalid or expired") from e

    def rotate(self, token: str) -> str:
        """Re-encrypt an existing token under the primary key.

        Decrypts with whichever configured key works, then re-encrypts with the
        first. Idempotent: a token already under the primary key comes back
        re-encrypted with a fresh IV and identical plaintext, so a re-encrypt
        pass can be re-run safely after an interruption.
        """
        try:
            return self._multi.rotate(token.encode("utf-8")).decode("utf-8")
        except InvalidToken as e:
            raise EncryptionError(
                "Rotation failed: no configured key could read this token."
            ) from e


def encryptor_from_settings(settings: "Settings") -> FernetEncryptor | None:
    """The engine's encryptor, or None when no RAG_ENCRYPTION_KEY is configured.

    Every reader of a corpus's stored LLM or embedding key goes through this. A
    ConfigResolver built without an encryptor cannot decrypt those keys and
    silently falls back to the engine default, so a corpus's own key was never
    used at query time.
    """
    if not parse_key_list(settings.encryption_key):
        return None
    return FernetEncryptor(settings.encryption_key)
