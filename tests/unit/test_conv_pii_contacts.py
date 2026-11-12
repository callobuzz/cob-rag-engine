"""TV2: contact details are hidden in owner-facing text when the visitor did not consent."""

import pytest

from callobuzz_rag.conversation.pii import mask_contacts


def test_phones_and_emails_are_hidden() -> None:
    assert mask_contacts("call me on 98220 12345 or +91-98220-12345, mail a@b.in") == (
        "call me on [phone hidden] or [phone hidden], mail [e-mail hidden]"
    )


@pytest.mark.parametrize(
    "text",
    [
        "020 2567 8901",
        "020-25678901",
        "+91 20 2567 8901",
        "09822012345",
        "+44 20 7946 0958",
        "0091 98220 12345",
    ],
)
def test_other_indian_and_international_shapes(text: str) -> None:
    assert mask_contacts(f"ring {text} please") == "ring [phone hidden] please"


@pytest.mark.parametrize(
    "text",
    [
        "Root canal costs Rs 1500",
        "fee is ₹12,000 only",
        "₹1,20,00,000 loan",
        "open at 10:30",
        "on 05/10/2026",
        "pin code 411001",
        "order 4521 and 7788",
        "Rs 7500 1234 5678",
    ],
)
def test_prices_times_dates_and_pin_codes_stay(text: str) -> None:
    assert mask_contacts(text) == text


def test_a_price_next_to_a_phone_number() -> None:
    assert mask_contacts("fee 1500 call 98220 12345") == "fee 1500 call [phone hidden]"


@pytest.mark.parametrize(
    "text",
    [
        "Invoice 9876543210 paid",
        "PNR 8523697410",
        "Order #0123456789",
        "booking no. 9876543210 confirmed",
        "ticket: 8523697410",
        "Ref ID 9123456780",
    ],
)
def test_order_invoice_and_booking_numbers_stay(text: str) -> None:
    assert mask_contacts(text) == text


def test_a_phone_number_after_other_words_is_still_hidden() -> None:
    assert mask_contacts("Invoice paid, call 9876543210") == "Invoice paid, call [phone hidden]"


@pytest.mark.parametrize(
    "text",
    [
        "my mob no 9822012345",
        "phone no: 98220 12345",
        "Ph. no. 9822012345",
        "whatsapp no +91 9822012345",
        "contact no 9822012345",
        "mobile id 9822012345",
    ],
)
def test_no_after_a_phone_word_is_a_phone_number(text: str) -> None:
    assert "[phone hidden]" in mask_contacts(text)
    assert "9822" not in mask_contacts(text)
