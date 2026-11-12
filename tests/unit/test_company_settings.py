import logging

import pytest
from pydantic import ValidationError

from callobuzz_rag.accounts.settings_store import (
    EngineSettings,
    SellerTaxProfile,
    _parse_stored,
    apply_changes,
    settings_diff,
)
from callobuzz_rag.core.company import STATES, CompanyDetails


def test_company_defaults() -> None:
    c = CompanyDetails()
    assert c.country == "IN"
    assert all(v is None for k, v in c.model_dump().items() if k != "country")


def test_company_rules() -> None:
    with pytest.raises(ValidationError):
        CompanyDetails(pincode="0123")
    with pytest.raises(ValidationError):
        CompanyDetails(state_code="99")
    assert CompanyDetails(state_code="27", pincode="411038").state_code == "27"
    assert STATES["27"] == "Maharashtra" and "97" in STATES


def test_address_lines() -> None:
    c = CompanyDetails(
        address_line1="Shop 4, Kothrud", city="Pune", state_code="27", pincode="411038"
    )
    assert c.address_lines() == ["Shop 4, Kothrud", "Pune 411038", "Maharashtra"]


def test_engine_settings_round_trip() -> None:
    s = EngineSettings(
        company=CompanyDetails(legal_name="Callobuzz", state_code="27"),
        invoicing=SellerTaxProfile(gstin="27AAPFU0939F1ZV", invoice_prefix="CB"),
    )
    again = _parse_stored(s.model_dump(mode="json"))
    assert again == s
    assert again.payments.min_top_up_minor == 50_000


def test_bad_stored_prefix_falls_back_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        s = _parse_stored({"invoicing": {"invoice_prefix": "TOOLONGX", "sac_default": "998315"}})
    assert s.invoicing.invoice_prefix == "INV"
    assert "ignoring invalid stored value" in caplog.text


def test_sac_must_start_99_and_gstin_is_checked() -> None:
    with pytest.raises(ValidationError):
        SellerTaxProfile(sac_default="123456")
    with pytest.raises(ValidationError):
        SellerTaxProfile(gstin="27ABCDE1234F1Z5")
    assert SellerTaxProfile(gstin=" 27aapfu0939f1zv ").gstin == "27AAPFU0939F1ZV"


def test_apply_changes_and_diff_for_the_new_sections() -> None:
    current = EngineSettings()
    new = apply_changes(
        current, {"company": {"legal_name": "Callobuzz", "city": "Pune"}}, allow_http_logo=False
    )
    assert new.company.legal_name == "Callobuzz"
    diff = settings_diff(current, new)
    assert set(diff) == {"company.legal_name", "company.city"}
    reset = apply_changes(new, {"company": {"city": None}}, allow_http_logo=False)
    assert reset.company.city is None
