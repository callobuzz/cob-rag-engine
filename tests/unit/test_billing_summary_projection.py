"""What the owner sees on Plan & billing before a bill arrives (TV3): the projected month-end and
the pure rules behind the renewal card and the coming price changes."""

from datetime import UTC, date, datetime

import pytest

from callobuzz_rag.billing.notice import IST, ist_midnight
from callobuzz_rag.billing.pricing import PricingFields
from callobuzz_rag.billing.summary import (
    coming_changes,
    project_month_end,
    projected_total,
    renewal_terms,
)
from callobuzz_rag.db.models.plan_pricing import PlanPricing


@pytest.mark.parametrize(
    "charged,day,days_in_month,expected",
    [
        (0, 5, 31, 0),
        (31_000, 1, 31, 961_000),
        (100_000, 10, 30, 300_000),
        (99_999, 30, 30, 99_999),
    ],
)
def test_projection(charged: int, day: int, days_in_month: int, expected: int) -> None:
    assert project_month_end(charged, day=day, days_in_month=days_in_month) == expected


def test_renewal_inside_the_month_adds_the_fee() -> None:
    p = projected_total(
        usage_projected=300_000,
        fee_minor=99_900,
        renews_on=date(2026, 10, 20),
        today=date(2026, 10, 10),
    )
    assert p == 399_900
    assert (
        projected_total(300_000, 99_900, renews_on=date(2026, 11, 2), today=date(2026, 10, 10))
        == 300_000
    )


def test_a_renewal_that_already_happened_or_is_unknown_adds_nothing() -> None:
    today = date(2026, 10, 10)
    assert projected_total(100, 99_900, renews_on=date(2026, 10, 10), today=today) == 100
    assert projected_total(100, 99_900, renews_on=None, today=today) == 100


def _version(number: int, effective_at: datetime | None, **fields: int) -> PlanPricing:
    values = PricingFields(
        subscription_fee_minor=fields.get("fee", 99_900),
        usage_mode="per_question",
        per_question_minor=fields.get("pq", 200),
        included_credit_minor=50_000,
    ).model_dump()
    return PlanPricing(
        plan_id=None, version=number, is_current=False, effective_at=effective_at, **values
    )


def test_renewal_shows_the_price_in_force_on_the_renewal_day() -> None:
    old = _version(1, None)
    new = _version(2, ist_midnight(date(2026, 11, 5)), fee=129_900)
    renews = date(2026, 11, 12)
    terms = renewal_terms([old, new], renews_on=renews, overrides=None, in_force=old)
    assert terms.amount_minor == 129_900 and terms.included_credit_minor == 50_000
    assert terms.new_price_from == renews
    # a renewal before the new price starts still shows the old one
    early = renewal_terms([old, new], renews_on=date(2026, 11, 2), overrides=None, in_force=old)
    assert early.amount_minor == 99_900 and early.new_price_from is None


def test_renewal_follows_the_customers_own_fee() -> None:
    old = _version(1, None)
    terms = renewal_terms(
        [old],
        renews_on=date(2026, 11, 12),
        overrides={"subscription_fee_minor": 50_000},
        in_force=old,
    )
    assert terms.amount_minor == 50_000


def test_coming_changes_list_each_money_difference_with_its_date() -> None:
    old = _version(1, None)
    new = _version(2, ist_midnight(date(2026, 11, 5)), fee=129_900, pq=300)
    out = coming_changes(
        [old, new],
        in_force=old,
        overrides=None,
        first_period_on=lambda eff: date(2026, 11, 12),
        plan_name="Growth",
    )
    assert len(out) == 1
    assert out[0]["effective_on"] == date(2026, 11, 12) and out[0]["plan_name"] == "Growth"
    assert out[0]["changes"] == [
        {"label": "Plan fee", "old_minor": 99_900, "new_minor": 129_900},
        {"label": "Price per question", "old_minor": 200, "new_minor": 300},
    ]


def test_a_version_already_in_force_is_not_coming() -> None:
    old = _version(1, None)
    new = _version(2, datetime(2026, 9, 1, tzinfo=UTC), fee=129_900)
    assert (
        coming_changes(
            [old, new],
            in_force=new,
            overrides=None,
            first_period_on=lambda e: date(2026, 11, 1),
            plan_name="G",
        )
        == []
    )


def test_ist_is_five_thirty_ahead() -> None:
    assert ist_midnight(date(2026, 11, 5)).astimezone(UTC) == datetime(
        2026, 11, 4, 18, 30, tzinfo=UTC
    )
    assert IST.utcoffset(None).total_seconds() == 19_800  # type: ignore[union-attr]
