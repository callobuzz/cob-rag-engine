"""`billing_state`, `enforce_billing`, `payer_hint` (BIL4 task 1)."""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from callobuzz_rag.billing import enforcement
from callobuzz_rag.billing.pricing import PricingFields, PricingOverrides, combine

TENANT = uuid.uuid4()


def P(**fields: Any) -> PricingFields:  # noqa: N802
    return PricingFields(**fields)


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    pricing: PricingFields,
    balance: int,
    pending_micro: int,
    override: str,
    *,
    exempt: bool = False,
    enabled: bool = True,
    account: bool = True,
) -> None:
    eff = combine(pricing, PricingOverrides(), currency="INR", exempt=exempt)

    async def fake_effective(session: Any, tenant_id: Any) -> Any:
        return eff

    async def fake_account(session: Any, tenant_id: Any) -> Any:
        if not account:
            return None
        return SimpleNamespace(id=uuid.uuid4(), balance_minor=balance, pause_override=override)

    async def fake_owed(session: Any, acct: Any) -> int:
        return pending_micro

    monkeypatch.setattr(enforcement, "effective_pricing", fake_effective)
    monkeypatch.setattr(enforcement, "_load_account", fake_account)
    monkeypatch.setattr(enforcement.ledger, "owed_micro", fake_owed)
    monkeypatch.setattr(enforcement, "billing_enabled", lambda: enabled)


@pytest.mark.parametrize(
    ("pricing", "payer", "balance", "pending_micro", "override", "expect"),
    [
        (P(), "operator", 0, 0, "auto", ("not_billed", False)),
        (
            P(usage_mode="per_question", per_question_minor=200),
            "operator",
            1,
            0,
            "auto",
            ("ok", False),
        ),
        (
            P(usage_mode="per_question", per_question_minor=200),
            "operator",
            0,
            0,
            "auto",
            ("prepaid_empty", True),
        ),
        (
            P(usage_mode="per_question", per_question_minor=200),
            "operator",
            1,
            10_001,
            "auto",
            ("prepaid_empty", True),
        ),
        (
            P(usage_mode="per_question", per_question_minor=200, grace_minor=500),
            "operator",
            -499,
            0,
            "auto",
            ("ok", False),
        ),
        (
            P(usage_mode="per_question", per_question_minor=200, grace_minor=500),
            "operator",
            -500,
            0,
            "auto",
            ("prepaid_empty", True),
        ),
        (
            P(usage_mode="cost_plus", payment_type="postpaid", credit_limit_minor=100_000),
            "operator",
            -99_999,
            0,
            "auto",
            ("ok", False),
        ),
        (
            P(usage_mode="cost_plus", payment_type="postpaid", credit_limit_minor=100_000),
            "operator",
            -100_000,
            0,
            "auto",
            ("credit_limit", True),
        ),
        (
            P(usage_mode="per_question", per_question_minor=200),
            "byok",
            -9_999,
            0,
            "auto",
            ("not_billed", False),
        ),
        (P(subscription_fee_minor=99_900), "byok", -99_900, 0, "auto", ("not_billed", False)),
        (
            P(usage_mode="per_question", per_question_minor=200),
            "operator",
            -10,
            0,
            "never",
            ("ok", False),
        ),
        (P(), "operator", 10_000, 0, "force", ("forced", True)),
        (P(), "byok", 10_000, 0, "force", ("forced", True)),
    ],
)
async def test_billing_state(
    monkeypatch: pytest.MonkeyPatch,
    pricing: PricingFields,
    payer: str,
    balance: int,
    pending_micro: int,
    override: str,
    expect: tuple[str, bool],
) -> None:
    _setup(monkeypatch, pricing, balance, pending_micro, override)
    state = await enforcement.billing_state(object(), TENANT, payer=payer)  # type: ignore[arg-type]
    assert (state.reason, state.paused) == expect


async def test_no_account_yet_counts_as_zero_balance(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(
        monkeypatch,
        P(usage_mode="per_question", per_question_minor=200),
        0,
        0,
        "auto",
        account=False,
    )
    state = await enforcement.billing_state(object(), TENANT, payer="operator")  # type: ignore[arg-type]
    assert state.paused and state.available_minor == 0


async def test_exempt_and_billing_off_are_never_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    pricing = P(usage_mode="per_question", per_question_minor=200)
    _setup(monkeypatch, pricing, -50, 0, "force", exempt=True)
    state = await enforcement.billing_state(object(), TENANT, payer="operator")  # type: ignore[arg-type]
    assert not state.paused and not state.billed
    _setup(monkeypatch, pricing, -50, 0, "force", enabled=False)
    state = await enforcement.billing_state(object(), TENANT, payer="operator")  # type: ignore[arg-type]
    assert not state.paused and not state.billed


async def test_admin_principal_is_never_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(monkeypatch, P(usage_mode="per_question", per_question_minor=200), -50, 0, "auto")
    admin = SimpleNamespace(state=SimpleNamespace(is_admin=True))
    state = await enforcement.enforce_billing(
        object(),
        admin,
        TENANT,
        payer="operator",
        surface="api",  # type: ignore[arg-type]
    )
    assert not state.paused
    customer = SimpleNamespace(state=SimpleNamespace(is_admin=False))
    state = await enforcement.enforce_billing(
        object(),
        customer,
        TENANT,
        payer="operator",
        surface="api",  # type: ignore[arg-type]
    )
    assert state.paused and state.reason == "prepaid_empty"


def test_payer_hint_reads_stored_key_and_headers_only_when_allowed() -> None:
    plain = SimpleNamespace(llm_config={"model": "x"})
    keyed = SimpleNamespace(llm_config={"api_key_encrypted": "abc"})
    with_header = SimpleNamespace(headers={"x-llm-api-key": "sk"})

    def hint(corpus: Any, request: Any, *, use_headers: bool) -> str:
        return enforcement.payer_hint(corpus, request, use_headers=use_headers, byok_allowed=True)

    assert hint(plain, None, use_headers=True) == "operator"
    assert hint(keyed, None, use_headers=False) == "byok"
    assert hint(plain, with_header, use_headers=True) == "byok"
    assert hint(plain, with_header, use_headers=False) == "operator"
    # P5 plans: a plan without the allowance pays by the operator's key, whatever is stored or sent
    for corpus, request, headers in ((keyed, None, False), (plain, with_header, True)):
        assert (
            enforcement.payer_hint(corpus, request, use_headers=headers, byok_allowed=False)
            == "operator"
        )
