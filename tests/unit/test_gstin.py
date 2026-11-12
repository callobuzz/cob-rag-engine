import pytest

from callobuzz_rag.billing.invoicing.gstin import GstinError, check, checksum_char, state_of


@pytest.mark.parametrize(
    "value", ["27AAPFU0939F1ZV", "29AAGCB7383J1Z4", "27ABCDE1234F1Z0", " 24abcde1234f1z6 "]
)
def test_valid(value: str) -> None:
    assert check(value) == value.strip().upper()


def _with_good_checksum(first_fourteen: str) -> str:
    return first_fourteen + checksum_char(first_fourteen)


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("27ABCDE1234F1Z5", "checksum"),
        ("27ABCDE1234F1Y0", "format"),
        (_with_good_checksum("99ABCDE1234F1Z"), "state"),
        ("27ABCDE1234F1Z", "format"),
        ("", "format"),
    ],
)
def test_invalid(value: str, reason: str) -> None:
    with pytest.raises(GstinError) as err:
        check(value)
    assert err.value.reason == reason


def test_state_of() -> None:
    assert state_of("27AAPFU0939F1ZV") == "27"
