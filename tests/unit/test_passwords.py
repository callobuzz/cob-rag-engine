"""accounts/passwords.py: argon2id, the policy, e-mail normalization (accounts A3)."""

import asyncio
import threading
import time

import pytest
from argon2 import PasswordHasher

from callobuzz_rag.accounts import passwords
from callobuzz_rag.accounts.passwords import (
    POLICY,
    hash_password,
    normalize_email,
    validate_password,
    verify_password,
)

GOOD = "violet-harbour-lantern"


def test_policy_limits() -> None:
    assert POLICY == {"min_length": 10, "max_length": 128}


@pytest.mark.parametrize(
    ("password", "email", "reasons"),
    [
        (GOOD, "asha@shree.example", []),
        ("short1", "asha@shree.example", ["too_short"]),
        ("x" * 129, "asha@shree.example", ["too_long"]),
        ("x" * 128, "asha@shree.example", []),
        ("1234567890", "asha@shree.example", ["common"]),
        ("QwertyUIOP", "asha@shree.example", ["common"]),  # case-insensitive
        ("password", "asha@shree.example", ["too_short", "common"]),
        ("my-ASHA.K-account-pw", "asha.k@shree.example", ["contains_email"]),
        ("the-ana-password-x", "ana@shree.example", []),  # local part under 4 chars
    ],
)
def test_policy_reasons(password: str, email: str, reasons: list[str]) -> None:
    assert validate_password(password, email=email) == reasons


def test_length_counts_code_points_after_nfkc() -> None:
    # "ﬀ" (U+FB00) is one code point that NFKC turns into "ff": 9 ligatures = 18 chars.
    assert validate_password("ﬀ" * 9, email="a@b.example") == []
    # Nine 4-byte emoji are 9 code points: too short however many bytes.
    assert "too_short" in validate_password("\U0001f600" * 9, email="a@b.example")


def test_bundled_list_is_the_10k_list_with_its_license() -> None:
    from importlib import resources

    data = resources.files("callobuzz_rag.accounts").joinpath("data")
    words = data.joinpath("common-passwords.txt").read_text("utf-8").split()
    assert 9_900 <= len(set(words)) <= 10_001  # SecLists ships 10,001 lines
    assert "MIT License" in data.joinpath("LICENSE-SecLists.txt").read_text("utf-8")


def test_normalize_email() -> None:
    assert normalize_email("  Asha@Shree.EXAMPLE ") == "asha@shree.example"
    fullwidth = "".join(chr(ord(c) + 0xFEE0) for c in "asha")  # U+FF41... fullwidth letters
    assert normalize_email(f"{fullwidth}@shree.example") == "asha@shree.example"


async def test_hash_is_argon2id_with_the_library_defaults() -> None:
    hashed = await hash_password(GOOD)
    assert hashed.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert await verify_password(GOOD, hashed) == (True, None)
    assert await verify_password("wrong-password-x", hashed) == (False, None)


async def test_verify_normalizes_like_hash() -> None:
    hashed = await hash_password("ﬀ" * 10)
    assert (await verify_password("ff" * 10, hashed))[0] is True


async def test_rehash_when_parameters_change() -> None:
    old = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(GOOD)
    ok, new_hash = await verify_password(GOOD, old)
    assert ok is True
    assert new_hash is not None and new_hash.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    # A wrong password never produces a new hash.
    assert await verify_password("nope-nope-nope", old) == (False, None)


async def test_no_stored_hash_runs_a_dummy_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    real = passwords._verify_sync

    def spy(password: str, stored: str) -> tuple[bool, bool]:
        seen.append(stored)
        return real(password, stored)

    monkeypatch.setattr(passwords, "_verify_sync", spy)
    assert await verify_password(GOOD, None) == (False, None)
    assert await verify_password(GOOD, "") == (False, None)
    assert len(seen) == 2 and all(s.startswith("$argon2id$") for s in seen)


async def test_malformed_stored_hash_is_a_mismatch() -> None:
    assert await verify_password(GOOD, "not-a-hash") == (False, None)


async def test_semaphore_limits_concurrency_to_four(monkeypatch: pytest.MonkeyPatch) -> None:
    active = 0
    peak = 0
    lock = threading.Lock()

    class SlowHasher:
        def hash(self, password: str) -> str:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return "$argon2id$stub"

    monkeypatch.setattr(passwords, "_hasher", SlowHasher())
    await asyncio.gather(*(hash_password(GOOD) for _ in range(12)))
    assert peak == passwords.HASH_CONCURRENCY == 4
