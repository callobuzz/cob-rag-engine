"""C1: identity numbers and secrets never reach a model, a log or a transcript."""

from callobuzz_rag.conversation.pii import (
    find_pii,
    luhn_valid,
    mask_pii,
    pii_notice,
    verhoeff_digit,
    verhoeff_valid,
)


def _aadhaar() -> str:
    body = "23456789012"
    return body + verhoeff_digit(body)


class TestChecksums:
    def test_verhoeff_round_trip(self) -> None:
        assert verhoeff_valid(_aadhaar())
        wrong = _aadhaar()[:-1] + str((int(_aadhaar()[-1]) + 1) % 10)
        assert not verhoeff_valid(wrong)

    def test_luhn(self) -> None:
        assert luhn_valid("4111111111111111")
        assert not luhn_valid("4111111111111112")


class TestMasking:
    def test_aadhaar_with_spaces(self) -> None:
        a = _aadhaar()
        text = f"My aadhaar is {a[:4]} {a[4:8]} {a[8:]} please check"
        out = mask_pii(text)
        assert "[Aadhaar hidden]" in out.text and a[:4] not in out.text
        assert out.kinds == ["aadhaar"]

    def test_twelve_digits_failing_checksum_are_kept(self) -> None:
        a = _aadhaar()
        wrong = a[:-1] + str((int(a[-1]) + 1) % 10)
        assert mask_pii(f"order {wrong}").text == f"order {wrong}"

    def test_pan(self) -> None:
        out = mask_pii("PAN: abcpk1234f for the invoice")
        assert "[PAN hidden]" in out.text and "1234" not in out.text

    def test_card(self) -> None:
        out = mask_pii("card 4111 1111 1111 1111 exp 12/29")
        assert "[card number hidden]" in out.text and "4111" not in out.text

    def test_otp_english_hindi_hinglish(self) -> None:
        assert "482913" not in mask_pii("my OTP is 482913").text
        assert "482913" not in mask_pii("ओटीपी 482913 है").text
        assert "482913" not in mask_pii("482913 mera otp hai").text

    def test_password(self) -> None:
        out = mask_pii("password: Shree@2026!")
        assert "Shree@2026" not in out.text and out.kinds == ["password"]

    def test_phone_numbers_and_prices_are_not_pii_here(self) -> None:
        text = "Call me on +91 98220 12345, budget ₹15,000"
        assert mask_pii(text).text == text
        assert find_pii(text) == []

    def test_several_kinds_in_order(self) -> None:
        out = mask_pii("PAN ABCPK1234F and OTP 1234")
        assert out.kinds == ["pan", "otp"]


def test_notice_is_localized() -> None:
    assert "Aadhaar" in pii_notice("en")
    assert "आधार" in pii_notice("hi")
    assert "Aadhaar" in pii_notice("hi-Latn")
    assert pii_notice("ta") == pii_notice("en")
