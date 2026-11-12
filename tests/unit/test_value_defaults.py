"""Suggested value settings per kind of business (TV4)."""

from typing import get_args

import pytest

from callobuzz_rag.business.value import ValueSettingsIn
from callobuzz_rag.business.value_defaults import known_business_types, suggested_for
from callobuzz_rag.conversation.schema import TemplateKey

KEYS = get_args(TemplateKey)


def test_every_template_key_has_an_inr_and_a_usd_row() -> None:
    assert sorted(known_business_types()) == sorted(KEYS)
    for currency in ("INR", "USD"):
        for key in KEYS:
            s = suggested_for(key, currency)
            for value in (s.lead_value_minor, s.handoff_value_minor, s.staff_hour_minor):
                assert type(value) is int and value >= 0
            assert type(s.minutes_per_answer) is int and 0 <= s.minutes_per_answer <= 120


def test_a_clinic_in_rupees() -> None:
    assert suggested_for("clinic", "INR") == ValueSettingsIn(150_000, 80_000, 25_000, 3)


@pytest.mark.parametrize("unknown", ["plumber", "", None])
def test_an_unknown_type_falls_back_to_generic_support(unknown: str | None) -> None:
    assert suggested_for(unknown, "INR") == suggested_for("generic_support", "INR")


def test_dollar_workspaces_get_cents() -> None:
    inr, usd = suggested_for("saas", "INR"), suggested_for("saas", "USD")
    assert usd.lead_value_minor < inr.lead_value_minor
    assert usd.minutes_per_answer == inr.minutes_per_answer


def test_the_suggestions_for_the_epics_table() -> None:
    assert suggested_for("store", "INR") == ValueSettingsIn(60_000, 30_000, 20_000, 2)
    assert suggested_for("coaching", "INR") == ValueSettingsIn(200_000, 50_000, 25_000, 4)
    assert suggested_for("real_estate", "INR") == ValueSettingsIn(500_000, 150_000, 30_000, 5)
    assert suggested_for("restaurant", "INR") == ValueSettingsIn(40_000, 25_000, 15_000, 2)
    assert suggested_for("saas", "INR") == ValueSettingsIn(300_000, 100_000, 40_000, 4)
    assert suggested_for("government", "INR") == ValueSettingsIn(0, 0, 25_000, 5)
    assert suggested_for("generic_support", "INR") == ValueSettingsIn(100_000, 50_000, 25_000, 3)
