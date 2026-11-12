"""TV2: reference codes, pure parts."""

import random
import re

import pytest

from callobuzz_rag.conversation.handoff.reference import (
    REF_ALPHABET,
    new_ref,
    normalise_ref,
    ref_prefix,
)


@pytest.mark.parametrize(
    ("name", "prefix"),
    [
        ("Shree Dental Clinic", "SD"),
        ("shree", "SH"),
        ("A", "RF"),
        ("", "RF"),
        ("श्री डेंटल", "RF"),
        ("Café Mocha Pune", "CM"),
        ("  Dr. Mehta's Clinic ", "DM"),
    ],
)
def test_prefix(name: str, prefix: str) -> None:
    assert ref_prefix(name) == prefix


def test_codes_use_only_the_unambiguous_alphabet() -> None:
    rng = random.Random(7)
    for _ in range(5_000):
        code = new_ref("SD", rng)
        assert re.fullmatch(r"SD-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{4}", code)
    assert not set("01ILOU") & set(REF_ALPHABET)
    assert len(REF_ALPHABET) == 30


@pytest.mark.parametrize(
    ("raw", "norm"),
    [
        ("SD-4F7K", "SD-4F7K"),
        ("sd4f7k", "SD-4F7K"),
        ("SD 4F7K", "SD-4F7K"),
        (" sd-4f7k. ", "SD-4F7K"),
        ("SD-4F7O", None),
        ("SD-4F71", None),
        ("SD-4F7", None),
        ("S-4F7KK", None),
        ("SD-4F7K9", None),
        ("", None),
    ],
)
def test_normalise(raw: str, norm: str | None) -> None:
    assert normalise_ref(raw) == norm
