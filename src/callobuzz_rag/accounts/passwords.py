"""Passwords: argon2id hashing, the password policy and e-mail normalization (accounts A3).

Hashing uses argon2-cffi's `PasswordHasher()` defaults, the RFC 9106 low-memory profile
(t=3, m=64 MiB, p=4, 32-byte hash, 16-byte salt, argon2id), which move with the library;
`verify_password` reports when a stored hash was made with other parameters so login can
rehash it. Every hash and verify runs in a worker thread behind a 4-slot semaphore: at
64 MiB each, a login burst must neither block the event loop nor balloon memory.

API keys stay bcrypt (`core/secrets.py`). Never mix the two.

Policy (NIST SP 800-63B: length and a blocklist, no composition rules): 10-128 Unicode
code points after NFKC normalization; not one of the 10,000 most common passwords
(SecLists, MIT, `data/common-passwords.txt`), compared case-insensitively; must not
contain the e-mail's local part when that is 4 or more characters.
"""

import asyncio
import secrets
import unicodedata
import weakref
from functools import cache
from importlib import resources
from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

POLICY: Final = {"min_length": 10, "max_length": 128}
HASH_CONCURRENCY: Final = 4

_hasher = PasswordHasher()
_semaphores: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)
_dummy_hash: str | None = None


def normalize_email(raw: str) -> str:
    """The stored form of an address: NFKC, trimmed, lower-cased. Used before every
    lookup and uniqueness check."""
    return unicodedata.normalize("NFKC", raw).strip().lower()


def _normalize_password(password: str) -> str:
    return unicodedata.normalize("NFKC", password)


@cache
def _common_passwords() -> frozenset[str]:
    text = (
        resources.files("callobuzz_rag.accounts")
        .joinpath("data/common-passwords.txt")
        .read_text("utf-8")
    )
    return frozenset(line.strip().lower() for line in text.splitlines() if line.strip())


def validate_password(password: str, *, email: str) -> list[str]:
    """Why `password` fails the policy: `too_short`, `too_long`, `common`,
    `contains_email`. An empty list means it passes."""
    pw = _normalize_password(password)
    reasons: list[str] = []
    if len(pw) < POLICY["min_length"]:
        reasons.append("too_short")
    if len(pw) > POLICY["max_length"]:
        reasons.append("too_long")
    lowered = pw.lower()
    if lowered in _common_passwords():
        reasons.append("common")
    local = normalize_email(email).split("@", 1)[0]
    if len(local) >= 4 and local in lowered:
        reasons.append("contains_email")
    return reasons


def _semaphore() -> asyncio.Semaphore:
    """One semaphore per event loop (a semaphore is bound to the loop it first waits on)."""
    loop = asyncio.get_running_loop()
    sem = _semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(HASH_CONCURRENCY)
        _semaphores[loop] = sem
    return sem


async def hash_password(password: str) -> str:
    """argon2id hash of the NFKC-normalized password."""
    pw = _normalize_password(password)
    async with _semaphore():
        return await asyncio.to_thread(_hasher.hash, pw)


def _verify_sync(password: str, stored: str) -> tuple[bool, bool]:
    try:
        _hasher.verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, False
    return True, _hasher.check_needs_rehash(stored)


async def _dummy() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = await hash_password(secrets.token_urlsafe(24))
    return _dummy_hash


async def verify_password(password: str, stored: str | None) -> tuple[bool, str | None]:
    """(matches, a new hash when the stored one should be rehashed).

    With no stored hash (unknown user, or no password yet) it verifies against a dummy
    hash and returns False, so the time taken does not reveal whether the account exists.
    """
    pw = _normalize_password(password)
    target = stored if stored else await _dummy()
    async with _semaphore():
        ok, needs_rehash = await asyncio.to_thread(_verify_sync, pw, target)
    if not stored or not ok:
        return False, None
    if needs_rehash:
        return True, await hash_password(pw)
    return True, None
