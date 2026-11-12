"""What the owner's view and the admin lists say about a pause (BIL4 task 6)."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from callobuzz_rag.api.routes.billing import live_state
from callobuzz_rag.billing.enforcement import stored_reason, stored_status
from callobuzz_rag.billing.pricing import PricingFields, PricingOverrides, combine


def eff(*, exempt: bool = False, **fields: Any) -> Any:
    return combine(PricingFields(**fields), PricingOverrides(), currency="INR", exempt=exempt)


def acct(**kw: Any) -> Any:
    base = {
        "pause_override": "auto",
        "paused_at": None,
        "alert_level": None,
        "balance_minor": 0,
        "last_top_up_minor": 10_000,
        "tenant_id": None,
    }
    return SimpleNamespace(**{**base, **kw})


PER_Q = {"usage_mode": "per_question", "per_question_minor": 200}


@pytest.mark.parametrize(
    ("kw", "e", "expect"),
    [
        ({}, eff(**PER_Q), "active"),
        ({"alert_level": 20}, eff(**PER_Q), "low"),
        ({"alert_level": 0, "paused_at": datetime.now(UTC)}, eff(**PER_Q), "paused"),
        ({"pause_override": "force"}, eff(**PER_Q), "forced"),
        ({}, eff(), "not_billed"),
        ({"paused_at": datetime.now(UTC)}, eff(exempt=True, **PER_Q), "not_billed"),
    ],
)
def test_the_admin_badge(kw: dict[str, Any], e: Any, expect: str) -> None:
    assert stored_status(e, acct(**kw)) == expect


def test_the_pause_reason_follows_the_payment_type() -> None:
    paused = acct(paused_at=datetime.now(UTC))
    assert stored_reason(eff(**PER_Q), paused) == "prepaid_empty"
    assert (
        stored_reason(
            eff(usage_mode="cost_plus", payment_type="postpaid", credit_limit_minor=5), paused
        )
        == "credit_limit"
    )
    assert stored_reason(eff(**PER_Q), acct(pause_override="force")) == "forced"
    assert stored_reason(eff(**PER_Q), acct()) is None


def _ctx(e: Any, **kw: Any) -> Any:
    return SimpleNamespace(account=acct(**kw), tenant=SimpleNamespace(name="Shree"), effective=e)


def test_the_owner_view_is_paused_at_zero_and_low_above_it() -> None:
    assert live_state(_ctx(eff(**PER_Q)), 0) == {
        "paused": True,
        "pause_reason": "prepaid_empty",
        "alert_level": None,  # 0 means paused; the banner reads `paused`
    }
    low = live_state(_ctx(eff(**PER_Q)), 400)
    assert low == {"paused": False, "pause_reason": None, "alert_level": 5}
    assert live_state(_ctx(eff(**PER_Q)), 9_000) == {
        "paused": False,
        "pause_reason": None,
        "alert_level": None,
    }


def test_an_unbilled_workspace_has_no_state() -> None:
    assert live_state(_ctx(eff()), 0) == {}
    assert live_state(_ctx(eff(exempt=True, **PER_Q)), -5) == {}
