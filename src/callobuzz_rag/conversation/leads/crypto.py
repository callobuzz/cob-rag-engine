"""Encryption for lead contact data, and keyed hashes for exact search.

Every value a visitor typed into a lead form lives only inside a Fernet token
(`core/secrets.py`). `index_hash` is HMAC-SHA256 of a normalised phone or e-mail,
keyed by a key derived from the PRIMARY Fernet key, so the inbox can find
"+919876543210" without storing it. `index_hashes` returns one hash per configured
key so a search still matches rows written under the old key mid-rotation;
`core/reencrypt.py` recomputes stored hashes under the primary key.
"""

import hashlib
import hmac
import json
from typing import TYPE_CHECKING, Any

from callobuzz_rag.core.exceptions import RAGKernelError
from callobuzz_rag.core.secrets import FernetEncryptor, parse_key_list

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings


class LeadCryptoUnavailableError(RAGKernelError):
    """RAG_ENCRYPTION_KEY is not set: leads cannot be stored or read."""


def _index_key(fernet_key: str) -> bytes:
    return hashlib.sha256(f"lead-index:{fernet_key}".encode()).digest()


def _hash(key: bytes, value: str) -> str:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


class LeadCrypto:
    def __init__(self, encryptor: FernetEncryptor) -> None:
        self.encryptor = encryptor
        self._index_key = _index_key(encryptor.primary_key)
        self._all_index_keys = [_index_key(k) for k in encryptor.keys]

    def encrypt_json(self, data: dict[str, Any]) -> str:
        return self.encryptor.encrypt(json.dumps(data, ensure_ascii=False, sort_keys=True))

    def decrypt_json(self, token: str) -> dict[str, Any]:
        value = json.loads(self.encryptor.decrypt(token))
        if not isinstance(value, dict):
            raise ValueError("lead payload is not an object")
        return value

    def encrypt_text(self, value: str | None) -> str | None:
        return None if value is None else self.encryptor.encrypt(value)

    def decrypt_text(self, token: str | None) -> str | None:
        return None if token is None else self.encryptor.decrypt(token)

    def index_hash(self, value: str | None) -> str | None:
        """The hash written to the row: always the primary key's."""
        if not value:
            return None
        return _hash(self._index_key, value)

    def index_hashes(self, value: str | None) -> list[str]:
        """Every hash a stored row might carry (one per configured key), for filtering."""
        if not value:
            return []
        return [_hash(k, value) for k in self._all_index_keys]


def lead_crypto_from_settings(settings: "Settings") -> LeadCrypto:
    if not parse_key_list(settings.encryption_key):
        raise LeadCryptoUnavailableError("Leads need RAG_ENCRYPTION_KEY")
    return LeadCrypto(FernetEncryptor(settings.encryption_key))
