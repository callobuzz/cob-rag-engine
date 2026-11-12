"""P5 T8: the seed for a hosted service is valid through the admin API's own models and its
content is what the plan says. Writing it (dry run, apply, run again) is tested on PostgreSQL in
`tests/integration/test_saas_plans_db.py`."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from callobuzz_rag.api.schemas.billing import PlanPricingRequest
from callobuzz_rag.api.schemas.billing_addons import CreateAddOnRequest
from callobuzz_rag.api.schemas.plan import CreatePlanRequest
from callobuzz_rag.billing import seed_saas
from callobuzz_rag.billing.seed_saas import SEED_FILE, load_seed, parse_seed
from callobuzz_rag.fairuse.limits import FairUseLimits


def test_seed_file_validates_through_the_admin_request_models() -> None:
    data = load_seed()
    assert [p["name"] for p in data["plans"]] == ["Free", "Standard", "Pro"]
    for p in data["plans"]:
        CreatePlanRequest.model_validate(p["plan"])
        PlanPricingRequest.model_validate(p["pricing"])
        assert p["plan"]["monthly_questions"] is None  # SV: no question caps
    by_name = {p["name"]: p for p in data["plans"]}
    assert by_name["Free"]["pricing"]["subscription_fee_minor"] == 0
    assert by_name["Free"]["pricing"]["included_credit_minor"] > 0
    assert (
        by_name["Free"]["plan"]["byok_allowed"],
        by_name["Standard"]["plan"]["byok_allowed"],
        by_name["Pro"]["plan"]["byok_allowed"],
    ) == (False, False, True)
    kinds = {a["kind"] for a in data["add_ons"]}
    assert kinds == {"credit_pack", "feature_unlock", "service"}
    assert all(a["sold_to_owners"] and a["placeholder"] for a in data["add_ons"])
    for a in data["add_ons"]:
        # placeholder is set by the seed only: the request model does not take it
        CreateAddOnRequest.model_validate({k: v for k, v in a.items() if k != "placeholder"})
    assert parse_seed(data)  # and the module's own parse agrees


def test_the_amounts_are_the_ones_in_the_plan() -> None:
    seed = parse_seed(load_seed())
    plans = {p.name: p.pricing for p in seed.plans}
    assert (plans["Free"].included_credit_minor, plans["Free"].per_question_minor) == (5_000, 50)
    assert (plans["Standard"].subscription_fee_minor, plans["Standard"].included_credit_minor) == (
        99_900,
        50_000,
    )
    assert plans["Standard"].per_question_minor == 50
    assert (plans["Pro"].subscription_fee_minor, plans["Pro"].included_credit_minor) == (
        499_900,
        300_000,
    )
    assert plans["Pro"].per_question_minor == 40
    for pricing in plans.values():
        assert pricing.usage_mode == "per_question" and pricing.payment_type == "prepaid"
        assert pricing.currency == "INR" and pricing.included_credit_expires is True
    by_name = {a.name: a for a in seed.add_ons}
    assert by_name["Use your own AI key"].price_minor == 49_900
    assert (
        by_name["Use your own AI key"].renews and by_name["Use your own AI key"].feature == "byok"
    )
    help_ = by_name["Installation help"]
    assert (help_.price_minor, help_.sac_code, help_.renews) == (199_900, "998313", False)
    design = by_name["Website design + deploy (single page) with full support"]
    assert (design.price_minor, design.includes_support_months, design.sac_code) == (
        1_499_900,
        3,
        "998314",
    )
    support = by_name["Priority support (monthly)"]
    assert (support.price_minor, support.renews, support.sac_code) == (99_900, True, "998313")
    packs = {a.credit_minor: a.bonus_pct for a in seed.add_ons if a.kind == "credit_pack"}
    assert packs == {50_000: 0, 100_000: 5, 500_000: 10}


def test_free_is_protected_by_fair_use_not_by_a_question_cap() -> None:
    seed = parse_seed(load_seed())
    free = next(p for p in seed.plans if p.name == "Free").plan
    assert free.monthly_questions is None
    limits = FairUseLimits.model_validate(free.fair_use_limits.model_dump(exclude_none=True))
    assert limits.questions_per_hour and limits.questions_per_day


def test_a_seed_the_api_would_refuse_is_refused_before_anything_is_made() -> None:
    data = load_seed()
    broken = json.loads(json.dumps(data))
    broken["add_ons"][0]["feature"] = None  # an unlock names its feature
    with pytest.raises(ValidationError):
        parse_seed(broken)
    typo = json.loads(json.dumps(data))
    typo["plans"][0]["pricing"]["subscription_fee_minr"] = 1
    with pytest.raises(ValidationError):
        parse_seed(typo)


def test_the_bundled_file_is_where_the_command_reads_it() -> None:
    assert SEED_FILE.is_file() and SEED_FILE.name == "saas_seed.json"
    assert Path(seed_saas.__file__).parent == SEED_FILE.parent.parent


def test_the_command_has_the_two_options(capsys: Any) -> None:
    with pytest.raises(SystemExit) as exc:  # --help exits 0
        seed_saas.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--apply" in out and "--file" in out


def test_an_invalid_file_exits_one_and_says_why(tmp_path: Path, capsys: Any) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    assert seed_saas.main(["--file", str(bad)]) == 1
    assert "Seeding failed" in capsys.readouterr().out
