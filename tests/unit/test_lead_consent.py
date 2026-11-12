import hashlib
import subprocess
import sys
import uuid

import pytest

from callobuzz_rag.conversation.leads.consent import (
    DEFAULT_NOTICES,
    consent_label,
    consent_row,
    notice_for,
    wording_hash,
)
from callobuzz_rag.conversation.schema import AssistantConfig


def _config(**leads_consent: object) -> AssistantConfig:
    raw = AssistantConfig().model_dump()
    raw["identity"]["business_name"] = "Shree Labs"
    raw["identity"]["card"]["email"] = "care@shree-labs.in"
    raw["leads"]["consent"].update(leads_consent)
    return AssistantConfig.model_validate(raw)


def test_schema_default_is_the_dpdp_notice() -> None:
    assert AssistantConfig().leads.consent.notice == DEFAULT_NOTICES
    assert set(DEFAULT_NOTICES) == {"en", "hi", "hi-Latn"}
    assert all(len(text) <= 1500 for text in DEFAULT_NOTICES.values())


def test_english_notice_names_purpose_retention_contact_and_withdrawal() -> None:
    text = notice_for(_config(retention_days=180), "en")
    assert "Shree Labs" in text and "180 days" in text and "care@shree-labs.in" in text
    assert "withdraw" in text and "for this purpose: To contact you about your enquiry." in text
    assert "only To contact" not in text and "Data Protection Board" in text
    assert "{" not in text


def test_hindi_hinglish_and_fallback() -> None:
    assert "सहमति" in notice_for(_config(), "hi")
    assert "sahmati" in notice_for(_config(), "hi-Latn")
    assert notice_for(_config(), "ta") == notice_for(_config(), "en")


def test_privacy_link_is_markdown() -> None:
    text = notice_for(_config(privacy_policy_url="https://shree-labs.in/privacy"), "en")
    assert text.endswith("[Privacy policy](https://shree-labs.in/privacy)")


def test_contact_falls_back_to_phone_then_a_word() -> None:
    raw = AssistantConfig().model_dump()
    raw["identity"]["business_name"] = "Shree Labs"
    assert "the business" in notice_for(AssistantConfig.model_validate(raw), "en")
    raw["identity"]["card"]["phone"] = "+919876543210"
    assert "+919876543210" in notice_for(AssistantConfig.model_validate(raw), "en")


def test_owner_braces_do_not_break_the_visitor_request() -> None:
    notice = {"en": "Hello {x} and } stray {business_name}, kept {retention_days} days {"}
    text = notice_for(_config(notice=notice), "en")
    assert text == "Hello {x} and } stray Shree Labs, kept 365 days {"


def test_hash_and_row() -> None:
    text = notice_for(_config(), "en")
    assert wording_hash(text) == hashlib.sha256(text.encode()).hexdigest()
    row = consent_row(
        lead_id=uuid.uuid4(),
        corpus_id=uuid.uuid4(),
        config=_config(),
        language="en",
        notice_text=text,
        ip="203.0.113.9",
    )
    assert row.notice_version == 2 and row.wording_hash == wording_hash(text)
    assert row.purpose == "To contact you about your enquiry."
    assert consent_label("hi").startswith("मैं")
    assert consent_label("ta") == consent_label("en")


@pytest.mark.parametrize(
    "module",
    [
        "callobuzz_rag.conversation.schema",
        "callobuzz_rag.conversation.leads.consent",
        "callobuzz_rag.api.schemas.assistant",
        "callobuzz_rag.conversation.offers",
    ],
)
def test_every_entry_point_imports_without_a_cycle(module: str) -> None:
    """`conversation/__init__` registers the lead offer; whichever module a process loads
    first, the packages must come up (the schema owns the notice text, consent imports it)."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr[-600:]
