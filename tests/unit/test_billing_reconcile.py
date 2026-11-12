"""The reconciliation rules over crafted facts (BIL3): a clean book is ok, each injected fault
yields exactly its finding, informational findings never fail a run."""

import uuid

import pytest

from callobuzz_rag.billing.money import MICRO_PER_MINOR
from callobuzz_rag.billing.reconcile import (
    AccountFacts,
    DayFacts,
    Finding,
    check_account,
    check_day,
    is_ok,
)

A, T = uuid.uuid4(), uuid.uuid4()


def facts(**kw: int | None) -> AccountFacts:
    base: dict[str, int | None] = {
        "cached_balance": 9_400,
        "cached_seq": 3,
        "residue_micro": 2,
        "ledger_count": 3,
        "ledger_sum": 9_400,
        "first_bad_seq": None,
        "posted_usage_minor": -600,
        "charged_micro": 600 * MICRO_PER_MINOR + 2 + 150,
        "unposted_micro": 150,
        "stale_unposted": 0,
        "live_lot_minor": 0,
    }
    base.update(kw)
    return AccountFacts(account_id=A, tenant_id=T, **base)  # type: ignore[arg-type]


def test_a_clean_book_has_no_findings() -> None:
    assert check_account(facts()) == []
    assert is_ok([])


@pytest.mark.parametrize(
    ("changes", "check"),
    [
        ({"first_bad_seq": 2}, "ledger_integrity"),
        ({"cached_balance": 9_401}, "ledger_integrity"),
        ({"cached_seq": 4}, "ledger_integrity"),
        ({"charged_micro": 600 * MICRO_PER_MINOR + 2 + 150 + 1}, "conservation"),
        ({"residue_micro": 3}, "conservation"),
        ({"stale_unposted": 4}, "stale_unposted"),
    ],
)
def test_each_fault_yields_exactly_its_finding(changes: dict[str, int], check: str) -> None:
    found = check_account(facts(**changes))
    assert [f.check for f in found] == [check]
    assert found[0].account_id == A and found[0].tenant_id == T
    assert not is_ok(found)


def test_the_first_bad_sequence_is_named() -> None:
    [finding] = check_account(facts(first_bad_seq=7))
    assert finding.detail == {"first_bad_seq": 7}


def test_lots_holding_more_than_the_balance_are_informational() -> None:
    [finding] = check_account(facts(live_lot_minor=10_000))
    assert finding.check == "lot_exceeds_balance" and finding.informational
    assert is_ok([finding])
    assert check_account(facts(live_lot_minor=9_400)) == []  # exactly the balance is fine
    assert (
        check_account(
            facts(cached_balance=0, ledger_sum=0, posted_usage_minor=-600, live_lot_minor=0)
        )
        == []
    )


def day(**kw: int | bool) -> DayFacts:
    base: dict[str, int | bool] = {
        "usage_requests": 10,
        "usage_cost_micro": 5_000,
        "charge_questions": 10,
        "charge_cost_micro": 5_000,
    }
    base.update(kw)
    return DayFacts(tenant_id=T, payer="operator", **base)  # type: ignore[arg-type]


def test_a_day_that_matches_has_no_findings() -> None:
    assert check_day(day()) == []


@pytest.mark.parametrize(
    "changes",
    [{"charge_questions": 9}, {"usage_requests": 11}, {"charge_cost_micro": 4_999}],
)
def test_a_missing_or_extra_charge_is_a_coverage_finding(changes: dict[str, int]) -> None:
    [finding] = check_day(day(**changes))
    assert finding.check == "coverage" and not finding.informational
    assert finding.tenant_id == T and finding.detail["payer"] == "operator"
    assert not is_ok([finding])


def test_a_pricing_change_explains_the_day() -> None:
    [finding] = check_day(day(charge_questions=0, pricing_changed=True))
    assert finding.check == "pricing_changed" and finding.informational


def test_a_pricing_change_on_a_day_that_adds_up_is_not_mentioned() -> None:
    assert check_day(day(pricing_changed=True)) == []


def test_a_deleted_knowledge_base_is_noted_not_failed() -> None:
    # its usage rows went with it; its charges are left out of the comparison, which matches
    [finding] = check_day(
        day(
            usage_requests=0,
            usage_cost_micro=0,
            charge_questions=0,
            charge_cost_micro=0,
            deleted_kb_charges=3,
        )
    )
    assert finding.check == "knowledge_base_deleted" and finding.informational
    assert finding.detail["charges"] == 3
    # a difference that remains after leaving them out is still a real one
    [real] = check_day(day(usage_requests=5, charge_questions=0, deleted_kb_charges=3))
    assert real.check == "coverage" and not real.informational


def test_informational_findings_never_fail_a_run() -> None:
    info = Finding("pricing_changed", informational=True)
    real = Finding("coverage")
    assert is_ok([info, info]) and not is_ok([info, real])
