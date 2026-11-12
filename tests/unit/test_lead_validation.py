import pytest

from callobuzz_rag.conversation.leads.validation import (
    LeadValidationError,
    normalize_email,
    normalize_phone,
    validate_submission,
)
from callobuzz_rag.conversation.schema import LeadField, Leads


@pytest.mark.parametrize(
    ("raw", "e164"),
    [
        ("9876543210", "+919876543210"),
        ("098765 43210", "+919876543210"),
        ("+91 98765-43210", "+919876543210"),
        ("91 9876543210", "+919876543210"),
        ("(+91) 6123456789", "+916123456789"),
        ("+971 50 123 4567", "+971501234567"),
        ("+44 7911 123456", "+447911123456"),
    ],
)
def test_phone_normalised(raw: str, e164: str) -> None:
    assert normalize_phone(raw) == e164


@pytest.mark.parametrize(
    "raw", ["5876543210", "98765", "98765432101", "abc", "+0 123", "+91 5876543210", ""]
)
def test_bad_phones(raw: str) -> None:
    with pytest.raises(LeadValidationError) as exc:
        normalize_phone(raw)
    assert exc.value.code == "lead_invalid_phone" and exc.value.field == "phone"


def test_email() -> None:
    assert normalize_email(" Priya@Shree-Labs.IN ") == "Priya@shree-labs.in"
    with pytest.raises(LeadValidationError) as exc:
        normalize_email("priya@")
    assert exc.value.code == "lead_invalid_email"


def _leads(**kw: object) -> Leads:
    return Leads(
        custom_fields=[
            LeadField(
                id="test_name",
                label={"en": "Test"},
                type="select",
                options=["Lipid profile", "HbA1c"],
                required=True,
            ),
            LeadField(id="visit_date", label={"en": "Preferred date"}, type="date"),
            LeadField(id="members", label={"en": "People"}, type="number"),
            LeadField(id="note", label={"en": "Note"}, type="text"),
        ],
        **kw,
    )


def test_full_submission() -> None:
    clean = validate_submission(
        _leads(),
        {
            "name": " Priya Kulkarni ",
            "phone": "98765 43210",
            "test_name": "HbA1c",
            "visit_date": "2026-10-15",
            "members": "2",
        },
    )
    assert clean.name == "Priya Kulkarni" and clean.phone == "+919876543210"
    assert clean.email is None
    assert clean.custom == {"test_name": "HbA1c", "visit_date": "2026-10-15", "members": 2.0}
    assert clean.as_payload()["custom"] == clean.custom


_OK = {"phone": "9876543210", "test_name": "HbA1c"}


@pytest.mark.parametrize(
    ("fields", "field", "reason"),
    [
        ({"test_name": "HbA1c"}, "phone", "required"),
        ({"phone": "9876543210"}, "test_name", "required"),
        ({**_OK, "test_name": "X-ray"}, "test_name", "not_an_option"),
        ({**_OK, "visit_date": "15/10"}, "visit_date", "not_a_date"),
        ({**_OK, "members": "two"}, "members", "not_a_number"),
        ({**_OK, "members": "nan"}, "members", "not_a_number"),
        ({**_OK, "members": "inf"}, "members", "not_a_number"),
        ({**_OK, "members": "-Infinity"}, "members", "not_a_number"),
        ({**_OK, "name": "x" * 121}, "name", "too_long"),
        ({**_OK, "unknown": "y"}, "unknown", "unknown_field"),
    ],
)
def test_field_errors(fields: dict[str, str], field: str, reason: str) -> None:
    with pytest.raises(LeadValidationError) as exc:
        validate_submission(_leads(), fields)
    assert (exc.value.field, exc.value.reason) == (field, reason)


def test_off_fields_are_refused_and_email_required_mode() -> None:
    with pytest.raises(LeadValidationError) as exc:
        validate_submission(_leads(name="off"), {**_OK, "name": "P"})
    assert exc.value.reason == "unknown_field"
    with pytest.raises(LeadValidationError) as exc:
        validate_submission(_leads(email="required", phone="optional"), {"test_name": "HbA1c"})
    assert exc.value.field == "email"
    assert exc.value.code == "lead_invalid_email"


def test_control_characters_are_replaced_in_name_and_text() -> None:
    clean = validate_submission(_leads(), {**_OK, "name": "Priya\r\nKulkarni", "note": "a\nb\tc"})
    assert clean.name == "Priya  Kulkarni"
    assert clean.custom["note"] == "a b c"


def test_custom_field_ids_cannot_shadow_the_built_in_fields() -> None:
    for reserved in ("name", "phone", "email"):
        with pytest.raises(ValueError, match="reserved"):
            Leads(custom_fields=[LeadField(id=reserved, label={"en": "X"}, type="text")])
