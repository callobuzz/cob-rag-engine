"""GSTIN checking: format, state code and the mod-36 checksum. Pure."""

import re
from typing import Final, Literal

from callobuzz_rag.core.company import STATES

_FORMAT: Final = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_ALPHABET: Final = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

Reason = Literal["format", "state", "checksum"]


class GstinError(ValueError):
    def __init__(self, reason: Reason) -> None:
        super().__init__(reason)
        self.reason: Reason = reason


def checksum_char(first_fourteen: str) -> str:
    """The 15th character for a GSTIN's first fourteen."""
    total = 0
    for index, char in enumerate(first_fourteen):
        product = _ALPHABET.index(char) * (1 if index % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _ALPHABET[(36 - total % 36) % 36]


def check(gstin: str) -> str:
    """The normalised GSTIN (stripped, upper case), or raises `GstinError` with the reason."""
    value = gstin.strip().upper()
    if not _FORMAT.match(value):
        raise GstinError("format")
    if value[:2] not in STATES:
        raise GstinError("state")
    if checksum_char(value[:14]) != value[14]:
        raise GstinError("checksum")
    return value


def state_of(gstin: str) -> str:
    return gstin.strip().upper()[:2]
