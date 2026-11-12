"""Plan pricing types and how a customer's overrides combine with them (billing BIL2)."""

import logging

import pytest
from pydantic import ValidationError

from callobuzz_rag.billing.pricing import (
    PricingFields,
    PricingOverrides,
    combine,
    overrides_from_stored,
)


def test_overrides_win_field_by_field_and_are_reported() -> None:
    plan = PricingFields(usage_mode="per_question", per_question_minor=200, payment_type="prepaid")
    eff = combine(
        plan,
        PricingOverrides(
            per_question_minor=150, payment_type="postpaid", credit_limit_minor=500_000
        ),
        currency="INR",
        exempt=False,
    )
    assert (eff.fields.per_question_minor, eff.fields.payment_type) == (150, "postpaid")
    assert eff.fields.usage_mode == "per_question"
    assert eff.overridden == {"per_question_minor", "payment_type", "credit_limit_minor"}


@pytest.mark.parametrize(
    ("fields", "payer", "expected"),
    [
        (PricingFields(), "operator", False),  # today's behaviour
        (PricingFields(usage_mode="cost_plus"), "operator", True),
        (PricingFields(overage="charge"), "operator", True),
        (PricingFields(subscription_fee_minor=99_900), "operator", True),
        (PricingFields(subscription_fee_minor=99_900), "byok", False),  # sub + free BYOK (D6)
        (PricingFields(byok_mode="per_question", byok_per_question_minor=50), "byok", True),
        (PricingFields(usage_mode="per_question", per_question_minor=5), "byok", False),
    ],
)
def test_chargeable(fields: PricingFields, payer: str, expected: bool) -> None:
    eff = combine(fields, PricingOverrides(), currency="INR", exempt=False)
    assert eff.chargeable(payer) is expected


def test_exempt_is_never_chargeable() -> None:
    eff = combine(
        PricingFields(usage_mode="per_question", per_question_minor=1),
        PricingOverrides(),
        currency="INR",
        exempt=True,
    )
    assert not eff.chargeable("operator")
    assert not eff.chargeable("byok")


def test_thresholds_validated_desc_unique_in_range() -> None:
    for bad in ([5, 20], [20, 20], [101], [-1], [50, 40, 30, 20, 10, 0]):
        with pytest.raises(ValidationError):
            PricingFields(alert_thresholds_pct=bad)
    assert PricingFields(alert_thresholds_pct=[]).alert_thresholds_pct == []
    with pytest.raises(ValidationError):
        PricingOverrides(alert_thresholds_pct=[1, 2])


def test_money_is_integers_only() -> None:
    for bad in (
        {"per_question_minor": 1.5},
        {"subscription_fee_minor": -1},
        {"markup_bp": 100_001},
    ):
        with pytest.raises(ValidationError):
            PricingFields(**bad)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        PricingFields(bogus=1)  # type: ignore[call-arg]


def test_stored_override_that_no_longer_validates_is_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        out = overrides_from_stored(
            {"usage_mode": "weird", "per_question_minor": 10, "gone_field": 1}
        )
    assert out.per_question_minor == 10
    assert out.usage_mode is None
    assert "ignoring" in caplog.text


def test_combine_uses_account_currency_and_defaults() -> None:
    eff = combine(PricingFields(currency="USD"), PricingOverrides(), currency="INR", exempt=False)
    assert eff.currency == "INR"
    assert eff.fields.currency == "INR"
    assert eff.pricing_id is None
    assert eff.overridden == frozenset()
