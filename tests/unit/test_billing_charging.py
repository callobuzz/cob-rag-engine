"""The pure price rule: `charge_amount` (README table), D2 FX, D3 overage, D4 unknown price."""

import random
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from callobuzz_rag.billing.charging import UsageEvent, charge_amount
from callobuzz_rag.billing.money import fx_ppm, usd_micro_to_micro
from callobuzz_rag.billing.pricing import PricingFields, PricingOverrides, combine

INR = fx_ppm(84.0, "INR")


def eff(**fields: Any):  # type: ignore[no-untyped-def]
    return combine(PricingFields(**fields), PricingOverrides(), currency="INR", exempt=False)


def ev(
    q: int = 1, cost: int = 0, before: int = 0, payer: str = "operator", known: bool = True
) -> UsageEvent:
    return UsageEvent(
        request_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        corpus_id=uuid.uuid4(),
        source="api",
        payer=payer,
        model="m",
        questions=q,
        cost_usd_micro=cost,
        cost_known=known,
        questions_before=before,
        at=datetime(2026, 10, 4, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("fields", "event", "limit", "expected"),
    [
        (dict(usage_mode="included"), ev(q=1, cost=2_000), None, (0, False)),
        (
            dict(usage_mode="per_question", per_question_minor=200),
            ev(q=1),
            None,
            (2_000_000, False),
        ),
        (
            dict(usage_mode="per_question", per_question_minor=200),
            ev(q=0, cost=900),
            None,
            (0, False),
        ),  # a helper call
        (
            dict(usage_mode="cost_plus", markup_bp=2500),
            ev(q=1, cost=1_000),
            None,
            (105_000, False),
        ),  # $0.001 -> Rs 0.084 -> +25 %
        (
            dict(usage_mode="cost_plus", markup_bp=0),
            ev(q=0, cost=1_000),
            None,
            (84_000, False),
        ),  # helpers too (D5)
        (
            dict(usage_mode="cost_plus", markup_bp=2500),
            ev(q=1, cost=1_000, known=False),
            None,
            (0, False),
        ),  # price unknown: 0, flagged on the row (D4)
        (
            dict(usage_mode="included", overage="charge", overage_per_question_minor=100),
            ev(q=1, before=999),
            1000,
            (0, False),
        ),  # the 1000th question is still within
        (
            dict(usage_mode="included", overage="charge", overage_per_question_minor=100),
            ev(q=1, before=1000),
            1000,
            (1_000_000, True),
        ),  # the 1001st is overage
        (
            dict(
                usage_mode="per_question",
                per_question_minor=200,
                overage="charge",
                overage_per_question_minor=300,
            ),
            ev(q=1, before=5),
            5,
            (3_000_000, True),
        ),  # instead of, not on top of (D3)
        (
            dict(
                usage_mode="cost_plus",
                markup_bp=1000,
                overage="charge",
                overage_mode="cost_plus",
            ),
            ev(q=1, cost=1_000, before=5),
            5,
            (92_400, True),
        ),
        (
            dict(usage_mode="per_question", per_question_minor=200, overage="allow"),
            ev(q=1, before=9),
            5,
            (2_000_000, False),
        ),
        (dict(byok_mode="free"), ev(q=1, payer="byok"), None, (0, False)),
        (
            dict(byok_mode="per_question", byok_per_question_minor=50),
            ev(q=1, payer="byok"),
            None,
            (500_000, False),
        ),
        (
            dict(
                byok_mode="per_question",
                byok_per_question_minor=50,
                overage="charge",
                overage_per_question_minor=80,
            ),
            ev(q=1, payer="byok", before=3),
            3,
            (800_000, True),
        ),
        (
            dict(
                byok_mode="per_question",
                byok_per_question_minor=50,
                byok_counts_toward_caps=False,
                overage="charge",
                overage_per_question_minor=80,
            ),
            ev(q=1, payer="byok", before=3),
            3,
            (500_000, False),
        ),
    ],
)
def test_charge_amount(
    fields: dict[str, Any], event: UsageEvent, limit: int | None, expected
) -> None:  # type: ignore[no-untyped-def]
    assert charge_amount(eff(**fields), event, fx=INR, monthly_limit=limit) == expected


def test_a_usd_account_charges_the_recorded_cost_one_to_one() -> None:
    usd = fx_ppm(84.0, "USD")
    p = combine(
        PricingFields(usage_mode="cost_plus", markup_bp=0, currency="USD"),
        PricingOverrides(),
        currency="USD",
        exempt=False,
    )
    assert charge_amount(p, ev(q=1, cost=1_234), fx=usd, monthly_limit=None) == (1_234, False)


@pytest.mark.parametrize("seed", range(200))
def test_cost_plus_is_monotone_in_cost_and_markup(seed: int) -> None:
    rng = random.Random(seed)
    c, m = rng.randint(0, 10**7), rng.randint(0, 50_000)

    def amount(cost: int, markup: int) -> int:
        return charge_amount(
            eff(usage_mode="cost_plus", markup_bp=markup),
            ev(q=1, cost=cost),
            fx=INR,
            monthly_limit=None,
        )[0]

    a = amount(c, m)
    assert a <= amount(c + 1, m)
    assert a <= amount(c, m + 1)
    assert a >= usd_micro_to_micro(c, INR) - 1


@pytest.mark.parametrize("seed", range(100))
def test_an_amount_is_never_negative(seed: int) -> None:
    rng = random.Random(seed)
    fields = dict(
        usage_mode=rng.choice(("included", "per_question", "cost_plus")),
        per_question_minor=rng.randint(0, 10**4),
        markup_bp=rng.randint(0, 10**5),
        overage=rng.choice(("block", "charge", "allow")),
        overage_mode=rng.choice(("per_question", "cost_plus")),
        overage_per_question_minor=rng.randint(0, 10**4),
    )
    event = ev(
        q=rng.choice((0, 1)),
        cost=rng.randint(0, 10**9),
        before=rng.randint(0, 20),
        payer=rng.choice(("operator", "byok")),
    )
    amount, _ = charge_amount(eff(**fields), event, fx=INR, monthly_limit=rng.choice((None, 10)))
    assert amount >= 0
