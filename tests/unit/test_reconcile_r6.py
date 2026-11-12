"""R6, the settlement check, as pure rules (P1 T9). The database side runs on PostgreSQL in
`tests/integration/test_payments_recon_db.py`."""

import uuid
from datetime import UTC, datetime

from callobuzz_rag.billing.payments.base import SettlementLine
from callobuzz_rag.billing.reconcile import (
    PaymentFact,
    SettlementFacts,
    check_settlements,
    working_days_before,
)

ACCOUNT, TENANT = uuid.uuid4(), uuid.uuid4()


def fact(provider_id: str = "pay_1", gross: int = 118_000) -> PaymentFact:
    return PaymentFact(uuid.uuid4(), TENANT, ACCOUNT, provider_id, gross)


def line(kind: str = "payment", entity: str = "pay_1", amount: int = 118_000) -> SettlementLine:
    return SettlementLine(
        kind, entity, amount, 2_832, 432, "setl_1", datetime(2026, 10, 7, tzinfo=UTC)
    )  # type: ignore[arg-type]


def facts(**kw: object) -> SettlementFacts:
    base: dict[str, object] = {
        "lines": [],
        "payments": {},
        "known_refunds": frozenset(),
        "unsettled": [],
        "entry_missing": [],
    }
    return SettlementFacts(**{**base, **kw})  # type: ignore[arg-type]


def test_a_settled_payment_we_hold_with_the_same_amount_is_fine() -> None:
    assert check_settlements(facts(lines=[line()], payments={"pay_1": fact()})) == []


def test_r6a_a_settled_payment_we_do_not_have() -> None:
    [finding] = check_settlements(facts(lines=[line(entity="pay_9")]))
    assert finding.check == "R6a" and finding.detail["provider_payment_id"] == "pay_9"
    assert not finding.informational


def test_r6c_a_different_amount_is_never_auto_fixed() -> None:
    [finding] = check_settlements(
        facts(lines=[line(amount=100_000)], payments={"pay_1": fact(gross=118_000)})
    )
    assert finding.check == "R6c"
    assert (finding.detail["recorded_minor"], finding.detail["settled_minor"]) == (118_000, 100_000)
    assert finding.tenant_id == TENANT and finding.account_id == ACCOUNT


def test_r6b_an_old_payment_that_was_never_settled() -> None:
    [finding] = check_settlements(facts(unsettled=[fact("pay_old")]))
    assert finding.check == "R6b" and finding.detail["reason"] == "settlement_missing"


def test_r6d_a_captured_payment_without_its_ledger_entry() -> None:
    [finding] = check_settlements(facts(entry_missing=[fact("pay_noentry")]))
    assert finding.check == "R6d" and finding.detail["reason"] == "ledger_entry_missing"


def test_r6e_a_refund_we_did_not_start_is_informational() -> None:
    ours = check_settlements(
        facts(lines=[line("refund", "rfnd_ours", 5_000)], known_refunds=frozenset({"rfnd_ours"}))
    )
    theirs = check_settlements(facts(lines=[line("refund", "rfnd_dashboard", 5_000)]))
    assert ours == []
    assert [(f.check, f.informational) for f in theirs] == [("R6e", True)]


def test_transfers_and_adjustments_are_not_judged() -> None:
    assert (
        check_settlements(facts(lines=[line("transfer", "trf_1"), line("adjustment", "adj_1")]))
        == []
    )


def test_working_days_skip_the_weekend() -> None:
    friday = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)  # a Friday
    assert friday.weekday() == 4
    assert working_days_before(friday, 3).date().isoformat() == "2026-10-06"  # Tuesday
    monday = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    assert working_days_before(monday, 3).date().isoformat() == "2026-10-07"  # Wednesday
    assert working_days_before(monday, 0) == monday
