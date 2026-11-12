"""GET /api/v1/home: the business dashboard's summary (BIZ3)."""

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.business.home import build_home, local_day_bounds, main_corpus
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.website.policy import WebsitePolicy

# 02:00 IST on 5 October is still 4 October in UTC.
NOW = datetime(2026, 10, 4, 20, 30, tzinfo=UTC)


def _corpus() -> Any:
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="Shree Dental Clinic")


def _scalars(rows: list[Any]) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    result.scalars.return_value.first.return_value = rows[0] if rows else None
    return result


def _one(*values: int) -> MagicMock:
    result = MagicMock()
    result.one.return_value = values
    return result


def _version(status: str, **identity: Any) -> Any:
    config = AssistantConfig.model_validate(
        {"enabled": identity.pop("enabled", True), "identity": identity}
    ).model_dump(mode="json")
    return SimpleNamespace(status=status, config=config)


def _site(origin: str, learned: int, status: str = "ready") -> Any:
    return SimpleNamespace(
        origin=origin,
        status=status,
        pages_learned=learned,
        last_run_finished_at=datetime(2026, 10, 4, 16, 0, tzinfo=UTC),
    )


async def _home(
    session_results: list[Any], *, policy: WebsitePolicy | None = None, corpus: Any = None
) -> tuple[Any, Any]:
    session = MagicMock()
    session.execute = AsyncMock(side_effect=session_results)
    settings = MagicMock()
    with patch(
        "callobuzz_rag.website.policy.website_policy",
        AsyncMock(return_value=policy or WebsitePolicy(True, None, 100)),
    ):
        out = await build_home(session, settings, corpus or _corpus(), now=NOW)
    return out, session


async def test_a_live_assistant_with_a_learned_website_and_a_busy_day() -> None:
    published = _version(
        "published",
        assistant_name="Shree Dental Assistant",
        business_name="Shree Dental Clinic, Pune",
        avatar_url="https://shreedental.in/bot.png",
    )
    out, _ = await _home(
        [
            _scalars([published]),
            _scalars([_site("https://shreedental.in", 12)]),
            _one(3, 1, 0),
        ]
    )
    assert out.assistant.enabled and out.assistant.published
    assert out.assistant.name == "Shree Dental Assistant"
    assert out.assistant.business_name == "Shree Dental Clinic, Pune"
    assert out.assistant.avatar_url == "https://shreedental.in/bot.png"
    assert out.website.learning is True
    assert [(s.origin, s.pages_learned, s.status) for s in out.website.sites] == [
        ("https://shreedental.in", 12, "ready")
    ]
    assert out.website.sites[0].last_updated_at is not None
    assert (out.today.conversations, out.today.leads, out.today.handoffs_open) == (3, 1, 0)


async def test_no_assistant_config_is_not_published() -> None:
    out, _ = await _home([_scalars([]), _scalars([]), _one(0, 0, 0)])
    assert out.assistant.published is False and out.assistant.enabled is False
    assert out.assistant.name is None
    assert out.website.sites == [] and out.today.timezone == "Asia/Kolkata"


async def test_a_draft_alone_shows_its_name_but_is_not_published() -> None:
    draft = _version("draft", assistant_name="Shree Dental Assistant")
    out, _ = await _home([_scalars([draft]), _scalars([]), _one(0, 0, 0)])
    assert out.assistant.published is False and out.assistant.enabled is False
    assert out.assistant.name == "Shree Dental Assistant"


async def test_a_switched_off_assistant_is_published_but_not_enabled() -> None:
    off = _version("published", assistant_name="Asha", enabled=False)
    out, _ = await _home([_scalars([off]), _scalars([]), _one(0, 0, 0)])
    assert out.assistant.published is True and out.assistant.enabled is False


async def test_website_learning_off_is_reported() -> None:
    out, _ = await _home(
        [_scalars([]), _scalars([]), _one(0, 0, 0)], policy=WebsitePolicy(False, "plan", 0)
    )
    assert out.website.learning is False


async def test_today_is_the_business_day_in_its_own_time_zone() -> None:
    kolkata = _version("published", assistant_name="Asha")
    out, session = await _home([_scalars([kolkata]), _scalars([]), _one(1, 2, 3)])
    assert out.today.timezone == "Asia/Kolkata"
    assert out.today.date == date(2026, 10, 5)  # 20:30 UTC is already 5 October in Pune
    # the day's bounds went into the counting query: 4 Oct 18:30 UTC to 5 Oct 18:30 UTC
    stmt = session.execute.await_args_list[-1].args[0]
    values = {v for v in stmt.compile().params.values() if isinstance(v, datetime)}
    assert datetime(2026, 10, 4, 18, 30, tzinfo=UTC) in values
    assert datetime(2026, 10, 5, 18, 30, tzinfo=UTC) in values


def test_a_lead_at_2330_ist_on_4_oct_is_not_today_on_5_oct() -> None:
    zone, start, end = local_day_bounds(NOW, "Asia/Kolkata")
    lead = datetime(2026, 10, 4, 18, 0, tzinfo=UTC)  # 23:30 IST on 4 October
    assert zone == "Asia/Kolkata"
    assert not (start <= lead < end)
    assert start <= datetime(2026, 10, 4, 19, 0, tzinfo=UTC) < end  # 00:30 IST on 5 October


def test_another_time_zone_and_an_unknown_one() -> None:
    zone, start, end = local_day_bounds(NOW, "America/New_York")
    assert zone == "America/New_York"
    assert start == datetime(2026, 10, 4, 4, 0, tzinfo=UTC)  # midnight EDT
    assert end - start == (end - start).__class__(hours=24)
    fallback, *_ = local_day_bounds(NOW, "Mars/Olympus")
    assert fallback == "Asia/Kolkata"


async def test_each_block_is_one_query() -> None:
    _, session = await _home([_scalars([]), _scalars([]), _one(0, 0, 0)])
    assert session.execute.await_count == 3  # assistant, sites, today (policy is separate)


async def test_a_business_with_no_knowledge_base_yet() -> None:
    session = MagicMock()
    session.execute = AsyncMock()
    out = await build_home(session, MagicMock(), None, now=NOW)
    assert out.kb_id is None and out.kb_name is None
    assert out.assistant.published is False and out.website.sites == []
    session.execute.assert_not_awaited()


async def test_main_corpus_is_the_oldest_non_system_one() -> None:
    session = MagicMock()
    oldest = _corpus()
    session.execute = AsyncMock(return_value=_scalars([oldest]))
    tenant_id = uuid.uuid4()
    assert await main_corpus(session, tenant_id) is oldest
    sql = str(session.execute.await_args.args[0])
    assert "corpora.is_system IS false" in sql.replace("= false", "IS false") or "is_system" in sql
    assert "ORDER BY corpora.created_at" in sql


# ----------------------------------------------------------------- the route


@pytest.mark.parametrize(
    ("key", "status", "code"),
    [
        ("admin", 403, "no_workspace"),
        ("sa", 403, "no_workspace"),
        ("ck_a", 403, "scope_forbidden"),
        (None, 401, None),
    ],
)
async def test_only_workspace_principals_have_a_home(
    key: str | None, status: int, code: str | None
) -> None:
    from tests.unit.scope_harness import call, scoped_app

    with scoped_app() as (app, _):
        res = await call(app, key, "GET", "/api/v1/home")
    assert res.status_code == status, res.text
    if code:
        assert res.json()["code"] == code


@pytest.mark.parametrize("key", ["tk_a", "owner_a", "member_a"])
async def test_a_workspace_reads_its_own_home(key: str) -> None:
    from tests.unit.scope_harness import call, scoped_app

    with scoped_app() as (app, world):
        res = await call(app, key, "GET", "/api/v1/home")  # no knowledge base in the stand-in
    assert res.status_code == 200, res.text
    assert res.json()["kb_id"] is None and res.json()["assistant"]["published"] is False
    assert world.corpus_a.id  # the stand-in world has a corpus; the empty query found none


@pytest.mark.parametrize("key", ["tk_a", "owner_a", "member_a"])
async def test_another_workspaces_knowledge_base_is_refused(key: str) -> None:
    from tests.unit.scope_harness import call, scoped_app

    with scoped_app() as (app, world):
        res = await call(app, key, "GET", f"/api/v1/home?corpus_id={world.corpus_b.id}")
    assert res.status_code == 403, res.text


# TV4 value
def _value_row(**kw: Any) -> Any:
    from callobuzz_rag.db.models.value import ValueSettings

    base: dict[str, Any] = {
        "tenant_id": uuid.uuid4(),
        "currency": "INR",
        "lead_value_minor": 150_000,
        "handoff_value_minor": 80_000,
        "staff_hour_minor": 25_000,
        "minutes_per_answer": 3,
        "email_enabled": True,
        "industry_defaults": None,
    }
    return ValueSettings(**{**base, **kw})


def _frozen(total: int | None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = (
        None if total is None else SimpleNamespace(data={"breakdown": {"total_minor": total}})
    )
    return result


async def _home_with_value(
    *,
    row: Any = None,
    trust_on: bool = True,
    frozen: int | None = 12_000,
    corpus: Any = "default",
    fail: bool = False,
) -> tuple[Any, Any]:
    from callobuzz_rag.accounts.settings_store import EngineSettings
    from callobuzz_rag.business.value import ValueInputs
    from callobuzz_rag.business.value_report import WorkspaceContext

    inputs = ValueInputs(40, 30, 412, 61, 12, 9, 2, 23, 4, 800, [], [])
    session = MagicMock()
    session.get = AsyncMock(return_value=row)
    results = [_scalars([]), _scalars([]), _one(3, 1, 0)]
    if trust_on and not fail:
        results.append(_frozen(frozen))
    results.append(_one(1, 0))  # TV5 nudges: the uploaded files and this month's conversations
    session.execute = AsyncMock(side_effect=results)
    engine = EngineSettings.model_validate({"trust": {"value_report_enabled": trust_on}})
    base = "callobuzz_rag.business.value_report."
    with (
        patch(
            "callobuzz_rag.website.policy.website_policy",
            AsyncMock(return_value=WebsitePolicy(True, None, 100)),
        ),
        patch("callobuzz_rag.onboarding.service.home_onboarding", AsyncMock(return_value=None)),
        patch("callobuzz_rag.onboarding.service.site_connected", AsyncMock(return_value=False)),
        patch(
            "callobuzz_rag.accounts.settings_store.read_engine_settings_row",
            AsyncMock(return_value=(engine, None)),
        ),
        patch(
            base + "workspace_context",
            AsyncMock(return_value=WorkspaceContext("clinic", "Asia/Kolkata", "INR")),
        ),
        patch(
            base + "fetch_inputs",
            AsyncMock(side_effect=RuntimeError("db") if fail else None, return_value=inputs),
        ),
    ):
        out = await build_home(
            session,
            MagicMock(),
            _corpus() if corpus == "default" else corpus,
            now=NOW,
            tenant_id=uuid.uuid4(),
        )
    return out, session


async def test_the_value_card_is_month_to_date_from_the_saved_settings() -> None:
    out, session = await _home_with_value(row=_value_row())
    assert out.value is not None
    month = out.value.month_to_date
    assert (month.answered, month.leads_qualified, month.handoffs) == (412, 9, 23)
    assert month.total_minor == 9 * 150_000 + 23 * 80_000 + 1_236 * 25_000 // 60
    assert out.value.saved_settings is True and month.currency == "INR"
    assert out.value.last_month_total_minor == 12_000
    # the base three blocks, the one frozen-month lookup and (TV5) the nudges' one query; the
    # counts are one patched group
    assert session.execute.await_count == 5


async def test_without_saved_settings_the_suggestions_are_used_and_flagged() -> None:
    out, _ = await _home_with_value(row=None, frozen=None)
    assert out.value is not None and out.value.saved_settings is False
    assert out.value.month_to_date.total_minor > 0
    assert out.value.last_month_total_minor is None


async def test_the_value_card_is_off_when_the_deployer_switches_it_off() -> None:
    out, session = await _home_with_value(row=_value_row(), trust_on=False)
    assert out.value is None
    assert session.execute.await_count == 4  # the base three and (TV5) the nudges' one


async def test_no_value_card_without_a_knowledge_base() -> None:
    out, session = await _home_with_value(row=_value_row(), corpus=None)
    assert out.value is None
    session.execute.assert_not_awaited()


async def test_a_failing_value_card_never_fails_the_page() -> None:
    out, _ = await _home_with_value(row=_value_row(), fail=True)
    assert out.value is None and out.today.conversations == 3


async def test_the_value_card_wording_never_promises() -> None:
    out, _ = await _home_with_value(row=_value_row())
    assert out.value is not None
    dumped = out.value.model_dump_json().lower()
    assert "roi" not in dumped and "guarantee" not in dumped


# /TV4 value
