"""LP3 T2: the pricing calculator in whole paise. The same cases file feeds calc.test.ts."""

import json
import random
from pathlib import Path

import pytest

from callobuzz_rag.landing.calc import CalcPlan, monthly_cost, recommend

ROOT = Path(__file__).parents[2]
CASES = json.loads((ROOT / "tests/fixtures/landing/calc_cases.json").read_text("utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_shared_cases(case: dict) -> None:
    plans = [CalcPlan(**p) for p in case["plans"]]
    got = recommend(plans, case["questions"], gst=case["gst"])
    assert (got.key if got else None) == case["expect"]["key"]
    if got:
        assert (got.monthly_minor, got.monthly_incl_minor) == (
            case["expect"]["monthly_minor"],
            case["expect"]["incl"],
        )


def random_plan(rng: random.Random) -> CalcPlan:
    mode = rng.choice(["included", "per_question", "cost_plus"])
    cap = rng.choice([None, rng.randint(1, 5000)])
    return CalcPlan(
        key="0:INR:monthly",
        fee_minor=rng.randint(0, 1_000_000),
        included_credit_minor=rng.randint(0, 500_000),
        usage_mode=mode,  # type: ignore[arg-type]
        per_question_minor=rng.randint(0, 500) if mode == "per_question" else 0,
        question_cap=cap,
        overage_charge_minor=rng.choice([None, rng.randint(0, 500)]) if cap else None,
        est_answer_minor=rng.randint(1, 500) if mode == "cost_plus" else None,
    )


def test_seeded_random_properties() -> None:
    rng = random.Random(20261010)
    for _ in range(2000):
        plan = random_plan(rng)
        q = rng.randint(0, 100_000)
        a, b = monthly_cost(plan, q, gst=False), monthly_cost(plan, q + 1, gst=False)
        assert type(a.monthly_minor) is int and a.monthly_minor >= plan.fee_minor
        if a.fits and b.fits:
            assert b.monthly_minor >= a.monthly_minor  # never cheaper for more questions
        assert monthly_cost(plan, q, gst=True).monthly_incl_minor >= a.monthly_minor


def test_recommend_never_picks_a_plan_that_does_not_fit() -> None:
    capped = CalcPlan("0:INR:monthly", 0, 0, "included", 0, 100, None, None)
    paid = CalcPlan("1:INR:monthly", 99_900, 50_000, "per_question", 200, None, None, None)
    assert recommend([capped, paid], 101, gst=False).key == "1:INR:monthly"  # type: ignore[union-attr]


def test_recommend_returns_none_when_nothing_fits() -> None:
    capped = CalcPlan("0:INR:monthly", 0, 0, "included", 0, 100, None, None)
    assert recommend([capped], 5000, gst=False) is None
    assert recommend([], 5, gst=False) is None


def test_no_float_in_the_module() -> None:
    import callobuzz_rag.landing.calc as module

    source = Path(module.__file__).read_text("utf-8")
    assert "float(" not in source and " / " not in source
