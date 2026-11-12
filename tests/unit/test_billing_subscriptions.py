"""The pure date rules of the billing schedule (BIL3); the flows are in
`tests/integration/test_billing_subscriptions_db.py`."""

from datetime import UTC, date, datetime, timedelta

import pytest

from callobuzz_rag.billing.subscriptions import (
    MAX_PERIODS_PER_RUN,
    add_period,
    clamp_day,
    due_dates,
    first_charge_on,
)


def test_monthly_charges_fall_on_the_billing_day() -> None:
    assert add_period(date(2026, 10, 5), "monthly", 5) == date(2026, 11, 5)
    assert add_period(date(2026, 12, 5), "monthly", 5) == date(2027, 1, 5)
    # the first charge can fall on any day; the next one is on the billing day
    assert add_period(date(2026, 10, 20), "monthly", 5) == date(2026, 11, 5)


def test_yearly_charges_keep_the_month() -> None:
    assert add_period(date(2026, 10, 5), "yearly", 5) == date(2027, 10, 5)
    assert add_period(date(2026, 3, 17), "yearly", 1) == date(2027, 3, 1)


@pytest.mark.parametrize(("given", "used"), [(0, 1), (1, 1), (28, 28), (29, 28), (31, 28)])
def test_the_billing_day_is_clamped(given: int, used: int) -> None:
    assert clamp_day(given) == used
    assert add_period(date(2026, 1, 1), "monthly", given).day == used


def test_a_missed_month_is_caught_up_in_order() -> None:
    dates = due_dates(date(2026, 10, 5), date(2026, 11, 6), "monthly", 5)
    assert dates == [date(2026, 10, 5), date(2026, 11, 5)]
    assert due_dates(date(2026, 12, 5), date(2026, 11, 6), "monthly", 5) == []  # nothing due yet


def test_a_catch_up_is_bounded() -> None:
    dates = due_dates(date(2020, 1, 5), date(2030, 1, 5), "monthly", 5)
    assert len(dates) == MAX_PERIODS_PER_RUN and dates == sorted(dates)


def test_a_trial_delays_the_first_charge() -> None:
    today = date(2026, 10, 5)
    trial_end = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
    assert first_charge_on(today, trial_end) == date(2026, 10, 19)
    assert first_charge_on(today, datetime.now(UTC) - timedelta(days=400)) == today  # over
    assert first_charge_on(today, None) == today


async def test_pending_usage_is_posted_before_the_schedule_judges_the_balance() -> None:
    import uuid
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from callobuzz_rag.billing import subscriptions
    from callobuzz_rag.billing.posting import BATCH_LIMIT

    account_id = uuid.uuid4()
    result = MagicMock()
    result.scalar_one_or_none.return_value = account_id
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.__aenter__.return_value = session
    full = SimpleNamespace(rows=BATCH_LIMIT, minor=500)
    rest = SimpleNamespace(rows=3, minor=0)
    post = AsyncMock(side_effect=[full, rest])
    hook = AsyncMock()
    with (
        patch.object(subscriptions.posting, "post_account", post),
        patch.object(subscriptions.hooks, "after_balance_change", hook),
    ):
        await subscriptions._post_pending_usage(lambda: session, uuid.uuid4(), datetime.now(UTC))
    assert post.await_count == 2  # a backlog bigger than one batch is drained
    hook.assert_awaited_once()  # the balance moved: tell the enforcement hook
