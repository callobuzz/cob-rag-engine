"""Uploads per day and storage (PIP3 task 5): the check next to the plan's upload size, and the
count the upload route writes. Records and website pages are not uploads."""

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.accounts.caps import check_upload_room
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.fairuse.limits import EffectiveFairUse, FairUseLimits, FairUsePolicy
from callobuzz_rag.fairuse.usage import FairUseUsage

MB = 1024 * 1024
NOW = datetime(2026, 10, 5, 14, 20, tzinfo=UTC)
TENANT = uuid.uuid4()


def limits(exempt: bool = False, **fields: int) -> EffectiveFairUse:
    return EffectiveFairUse(FairUseLimits(**fields), FairUsePolicy(), exempt, frozenset())


async def check(
    eff: EffectiveFairUse, usage: FairUseUsage, adding: int = 1 * MB, **kw: Any
) -> AsyncMock:
    reader = AsyncMock(return_value=usage)
    with (
        patch("callobuzz_rag.fairuse.limits.effective_fair_use", AsyncMock(return_value=eff)),
        patch("callobuzz_rag.fairuse.usage.read_usage", reader),
    ):
        await check_upload_room(AsyncMock(), TENANT, adding, now=NOW, **kw)
    return reader


async def test_uploads_per_day_are_429_once_used() -> None:
    eff = limits(uploads_per_day=2)
    await check(eff, FairUseUsage(uploads_per_day=1))  # the second is fine
    with pytest.raises(APIError) as err:
        await check(eff, FairUseUsage(uploads_per_day=2))
    e = err.value
    assert (e.status_code, e.code) == (429, "fair_use_limit")
    assert e.extra["metric"] == "uploads_per_day" and e.extra["limit"] == 2 and e.extra["used"] == 2
    assert e.extra["resets_at"] == "2026-10-06T00:00:00Z"
    assert e.headers["Retry-After"] == str(9 * 3600 + 40 * 60)
    assert "tomorrow" in e.detail


async def test_storage_is_409_when_the_file_would_pass_it() -> None:
    eff = limits(storage_mb=1)
    await check(eff, FairUseUsage(storage_bytes=0), adding=1 * MB)  # exactly the limit is fine
    with pytest.raises(APIError) as err:
        await check(eff, FairUseUsage(storage_bytes=1), adding=1 * MB)
    e = err.value
    assert (e.status_code, e.code) == (409, "storage_limit_reached")
    assert e.extra == {"limit_mb": 1, "used_mb": 1, "adding_mb": 1}


async def test_a_two_megabyte_brochure_does_not_fit_in_one_megabyte() -> None:
    with pytest.raises(APIError) as err:
        await check(limits(storage_mb=1), FairUseUsage(storage_bytes=0), adding=2 * MB)
    assert err.value.code == "storage_limit_reached" and err.value.extra["adding_mb"] == 2


async def test_the_day_is_checked_before_the_storage() -> None:
    eff = limits(uploads_per_day=1, storage_mb=1)
    with pytest.raises(APIError) as err:
        await check(eff, FairUseUsage(uploads_per_day=1, storage_bytes=5 * MB), adding=2 * MB)
    assert err.value.code == "fair_use_limit"


async def test_an_unlimited_business_costs_one_lookup_and_nothing_is_counted() -> None:
    reader = await check(limits(questions_per_hour=5), FairUseUsage())
    reader.assert_not_awaited()


async def test_only_the_limited_upload_metrics_are_read() -> None:
    reader = await check(limits(storage_mb=100), FairUseUsage(storage_bytes=1))
    assert reader.await_args.kwargs["metrics"] == ["storage_mb"]
    reader = await check(limits(uploads_per_day=10), FairUseUsage())
    assert reader.await_args.kwargs["metrics"] == ["uploads_per_day"]


async def test_the_operator_and_a_switched_off_engine_are_never_limited() -> None:
    reader = await check(limits(exempt=True, uploads_per_day=1), FairUseUsage(uploads_per_day=9))
    reader.assert_not_awaited()
    reader = await check(limits(uploads_per_day=1), FairUseUsage(uploads_per_day=9), enabled=False)
    reader.assert_not_awaited()
    await check_upload_room(AsyncMock(), None, 5 * MB)  # no workspace: nothing to check


# ------------------------------------------------------------------------ the upload route


def _upload_world() -> tuple[Any, Any, Any, Any]:
    from tests.unit.test_ingest_idempotency import _request

    return _request()


@pytest.mark.usefixtures("unlimited_plan")
async def test_an_upload_is_checked_and_then_counted_with_the_job() -> None:
    from tests.unit import test_ingest_idempotency as helpers

    request, file, session, corpus = _upload_world()
    session.execute = helpers._lookups(None)
    check_room, count = AsyncMock(), AsyncMock()
    with (
        patch("callobuzz_rag.api.routes.ingest.check_upload_room", check_room),
        patch("callobuzz_rag.api.routes.ingest._count_upload", count),
    ):
        await helpers._upload(request, file, session)
    check_room.assert_awaited_once()
    assert check_room.await_args.args[1] == corpus.tenant_id
    assert check_room.await_args.args[2] == len(helpers.CONTENT)  # the file's own size
    count.assert_awaited_once()
    assert count.await_args.args[1] == corpus.tenant_id


@pytest.mark.usefixtures("unlimited_plan")
async def test_a_refused_upload_is_not_counted() -> None:
    from tests.unit import test_ingest_idempotency as helpers

    request, file, session, _ = _upload_world()
    session.execute = helpers._lookups(None)
    refuse = AsyncMock(side_effect=APIError(429, "Used.", "fair_use_limit"))
    count = AsyncMock()
    with (
        patch("callobuzz_rag.api.routes.ingest.check_upload_room", refuse),
        patch("callobuzz_rag.api.routes.ingest._count_upload", count),
        pytest.raises(APIError),
    ):
        await helpers._upload(request, file, session)
    count.assert_not_awaited()


@pytest.mark.usefixtures("unlimited_plan")
async def test_a_replayed_upload_is_not_a_second_upload() -> None:
    from tests.unit import test_ingest_idempotency as helpers

    request, file, session, corpus = _upload_world()
    session.execute = helpers._lookups(helpers._job(corpus.id))
    count = AsyncMock()
    with patch("callobuzz_rag.api.routes.ingest._count_upload", count):
        await helpers._upload(request, file, session)
    count.assert_not_awaited()


async def test_the_upload_counter_adds_one_to_todays_row() -> None:
    from sqlalchemy.dialects import postgresql

    from callobuzz_rag.api.routes.ingest import _count_upload

    statements: list[Any] = []

    class S:
        async def execute(self, stmt: Any) -> None:
            statements.append(stmt)

    await _count_upload(S(), TENANT)  # type: ignore[arg-type]
    sql = str(statements[0].compile(dialect=postgresql.dialect()))
    assert (
        "INSERT INTO fair_use_counters" in sql
        and "ON CONFLICT (tenant_id, day, metric) DO UPDATE" in sql
    )
    assert statements[0].compile(dialect=postgresql.dialect()).params["metric"] == "uploads"


async def test_a_limited_business_is_checked_under_the_tenant_lock() -> None:
    """FU-4: parallel uploads each read the same day and storage numbers unless the check
    is serialised with the tenant lock that the job's commit releases."""
    session = AsyncMock()
    with (
        patch(
            "callobuzz_rag.fairuse.limits.effective_fair_use",
            AsyncMock(return_value=limits(uploads_per_day=5)),
        ),
        patch(
            "callobuzz_rag.fairuse.usage.read_usage",
            AsyncMock(return_value=FairUseUsage(uploads_per_day=0)),
        ),
        patch("callobuzz_rag.accounts.caps.lock_tenant_documents", AsyncMock()) as lock,
    ):
        await check_upload_room(session, TENANT, MB, now=NOW)
    lock.assert_awaited_once_with(session, TENANT)


async def test_an_unlimited_business_takes_no_lock() -> None:
    with (
        patch(
            "callobuzz_rag.fairuse.limits.effective_fair_use",
            AsyncMock(return_value=limits(questions_per_hour=5)),
        ),
        patch("callobuzz_rag.accounts.caps.lock_tenant_documents", AsyncMock()) as lock,
    ):
        await check_upload_room(AsyncMock(), TENANT, MB, now=NOW)
    lock.assert_not_awaited()
