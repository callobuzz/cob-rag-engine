"""Model-based property tests of the pure ledger, lot and posting functions (BIL3)."""

import random

import pytest

from callobuzz_rag.billing.ledger import EntryType, IdempotencyConflict, check_sign, replay
from callobuzz_rag.billing.lots import Lot, consume, expire_amount
from callobuzz_rag.billing.money import MICRO_PER_MINOR, MoneyError
from callobuzz_rag.billing.posting import post_batch
from tests.unit.billing_model import OPS, WalletModel, random_wallet


@pytest.mark.parametrize("seed", range(300))
def test_wallet_invariants_hold_for_any_sequence(seed: int) -> None:
    rng = random.Random(seed)
    w = WalletModel(currency="INR")
    for _ in range(rng.randint(1, 60)):
        w.apply(rng.choice(OPS), rng)
        assert w.balance == sum(e.amount for e in w.entries)  # I1
        assert [e.seq for e in w.entries] == list(range(1, len(w.entries) + 1))
        assert all(
            e.balance_after == sum(x.amount for x in w.entries[: i + 1])
            for i, e in enumerate(w.entries)
        )
        # I6: every charge is posted, carried or still pending, never lost or invented
        assert (
            w.charged_micro
            == -w.posted_usage_minor * MICRO_PER_MINOR + w.residue + w.unposted_micro
        )
        assert 0 <= w.residue < MICRO_PER_MINOR
        assert all(0 <= lot.remaining <= lot.amount for lot in w.lots)
        assert all(e.amount != 0 for e in w.entries)


@pytest.mark.parametrize("seed", range(300))
def test_replaying_an_idempotency_key_changes_nothing(seed: int) -> None:
    w = random_wallet(seed)
    before = (w.balance, len(w.entries))
    w.top_up(5_000, key=w.entries[0].key)  # a replay of the first key, same amount: no-op
    assert (w.balance, len(w.entries)) == before
    with pytest.raises(IdempotencyConflict):
        w.top_up(5_001, key=w.entries[0].key)


def _lot(amount: int, expires: int) -> Lot:
    return Lot(f"lot-{amount}-{expires}", amount, amount, expires)


def test_lots_consume_earliest_expiry_first_and_never_expire_absent_money() -> None:
    lots = [_lot(100, expires=10), _lot(50, expires=5)]
    left, used = consume(lots, 120)
    assert [lot.remaining for lot in left] == [30, 0] and used == 120
    assert expire_amount(remaining=30, balance=10) == 10
    assert expire_amount(30, balance=-5) == 0
    assert expire_amount(30, balance=500) == 30


def test_consume_takes_no_more_than_the_lots_hold() -> None:
    left, used = consume([_lot(40, expires=1)], 100)
    assert used == 40 and left[0].remaining == 0
    assert consume([], 5) == ([], 0)


@pytest.mark.parametrize(
    ("kind", "amount", "ok"),
    [
        (EntryType.TOP_UP, 1, True),
        (EntryType.TOP_UP, -1, False),
        (EntryType.USAGE_CHARGE, -1, True),
        (EntryType.USAGE_CHARGE, 1, False),
        (EntryType.ADJUSTMENT, -7, True),
        (EntryType.ADJUSTMENT, 7, True),
        (EntryType.ADJUSTMENT, 0, False),
        (EntryType.ADD_ON_PURCHASE, 9, True),
        (EntryType.ADD_ON_PURCHASE, -9, True),
        (EntryType.REFUND, -1, True),
        (EntryType.REFUND, 1, False),
        (EntryType.INCLUDED_CREDIT, 1, True),
        (EntryType.EXPIRY, 1, False),
    ],
)
def test_check_sign(kind: EntryType, amount: int, ok: bool) -> None:
    if ok:
        check_sign(kind, amount)
    else:
        with pytest.raises(MoneyError):
            check_sign(kind, amount)


def test_check_sign_refuses_floats_and_bools() -> None:
    with pytest.raises(MoneyError):
        check_sign(EntryType.TOP_UP, 1.5)  # type: ignore[arg-type]
    with pytest.raises(MoneyError):
        check_sign(EntryType.TOP_UP, True)  # type: ignore[arg-type]


def test_replay_is_the_running_sum() -> None:
    assert replay([5, -2, 10]) == [5, 3, 13]
    assert replay([1], start=9) == [10]
    assert replay([]) == []


def test_post_batch_carries_the_remainder() -> None:
    residue = 0
    minors: list[int] = []
    for _ in range(3):  # three batches of 3,334 micro: 0, 0, then one whole minor unit
        minor, residue = post_batch([3_334], residue)
        minors.append(minor)
    assert minors == [0, 0, 1] and residue == 2
    assert post_batch([1], 9_999) == (1, 0)
    assert post_batch([], 0) == (0, 0)
    assert post_batch([3_333, 3_333, 3_333], 0) == (0, 9_999)
