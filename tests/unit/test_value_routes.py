# ruff: noqa: RUF001
"""The value routes (TV4): settings (owner), the report with its formula, frozen months, the
printable page and the deployer's view."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.accounts.settings_store import EngineSettings, invalidate_engine_settings_cache
from callobuzz_rag.business.value import ValueInputs
from callobuzz_rag.business.value_report import WorkspaceContext
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.value import ValueReport, ValueSettings
from tests.unit.scope_harness import World, call, scoped_app

SERVICE = "callobuzz_rag.api.routes.value.service."
REPORT_MODULE = "callobuzz_rag.business.value_report."
CLINIC = WorkspaceContext("clinic", "Asia/Kolkata", "INR")
BODY = {
    "lead_value_minor": 150000,
    "handoff_value_minor": 80000,
    "staff_hour_minor": 25000,
    "minutes_per_answer": 3,
    "email_enabled": True,
}


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    invalidate_engine_settings_cache()
    yield
    invalidate_engine_settings_cache()


class Store:
    """The value tables behind the world's session: `session.get` and `session.add`."""

    def __init__(self, world: World) -> None:
        self.settings: dict[uuid.UUID, ValueSettings] = {}
        self.reports: list[ValueReport] = []
        original = world.session.get.side_effect

        async def get(model: Any, key: Any, *args: Any, **kwargs: Any) -> Any:
            if model is ValueSettings:
                return self.settings.get(key)
            return await original(model, key, *args, **kwargs)

        def add(obj: Any) -> None:
            if isinstance(obj, ValueSettings):
                self.settings[obj.tenant_id] = obj

        world.session.get = AsyncMock(side_effect=get)
        world.session.add = MagicMock(side_effect=add)
        self.world = world

    def audit(self) -> list[AuditLog]:
        return [
            c.args[0]
            for c in self.world.session.add.call_args_list
            if isinstance(c.args[0], AuditLog)
        ]


@contextmanager
def _app(context: WorkspaceContext = CLINIC) -> Iterator[tuple[Any, World, Store]]:
    with (
        scoped_app() as (app, world),
        patch(SERVICE + "workspace_context", AsyncMock(return_value=context)),
    ):
        yield app, world, Store(world)


def _inputs(**kw: Any) -> ValueInputs:
    base: dict[str, Any] = dict(
        conversations=40,
        questions=30,
        answered=412,
        answered_after_hours=9,
        leads_total=12,
        leads_qualified=9,
        leads_partial=2,
        handoffs=23,
        bookings=4,
        median_first_reply_ms=1800,
        top_questions=[("root canal ka kitna charge hai?", 7)],
        unanswered=[("do you take cards", 3)],
    )
    return ValueInputs(**{**base, **kw})


# ------------------------------------------------------------------ settings


async def test_get_settings_with_no_row_returns_the_suggestions() -> None:
    with _app() as (app, _world, _):
        res = await call(app, "owner_a", "GET", "/api/v1/value/settings")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["saved"] is False and body["currency"] == "INR"
    assert body["business_type"] == "clinic"
    assert body["lead_value_minor"] == 150_000 == body["suggested"]["lead_value_minor"]
    assert body["suggested"] == {
        "lead_value_minor": 150_000,
        "handoff_value_minor": 80_000,
        "staff_hour_minor": 25_000,
        "minutes_per_answer": 3,
    }


async def test_owner_saves_and_a_second_put_names_only_what_changed() -> None:
    with _app() as (app, world, store):
        res = await call(app, "owner_a", "PUT", "/api/v1/value/settings", json=BODY)
        assert res.status_code == 200, res.text
        assert res.json()["saved"] is True
        row = store.settings[world.tenant_a.id]
        assert (row.lead_value_minor, row.currency) == (150000, "INR")
        (first,) = store.audit()
        assert first.action == "value.settings_updated"
        assert first.details["changed"] == [
            "lead_value_minor",
            "handoff_value_minor",
            "staff_hour_minor",
            "minutes_per_answer",
        ]

        again = await call(
            app,
            "owner_a",
            "PUT",
            "/api/v1/value/settings",
            json={**BODY, "lead_value_minor": 200000},
        )
        assert again.status_code == 200, again.text
        assert store.audit()[-1].details["changed"] == ["lead_value_minor"]
        assert len(store.audit()) == 2

        same = await call(
            app,
            "owner_a",
            "PUT",
            "/api/v1/value/settings",
            json={**BODY, "lead_value_minor": 200000},
        )
        assert same.status_code == 200
        assert len(store.audit()) == 2  # nothing changed, nothing audited


async def test_the_industry_defaults_marker_is_stored_and_audited() -> None:
    with _app() as (app, _world, store):
        res = await call(
            app,
            "owner_a",
            "PUT",
            "/api/v1/value/settings",
            json={**BODY, "industry_defaults": "clinic"},
        )
    assert res.status_code == 200, res.text
    assert res.json()["industry_defaults"] == "clinic"
    assert store.audit()[-1].details["industry_defaults"] == "clinic"


async def test_a_member_cannot_save() -> None:
    with _app() as (app, _, _):
        res = await call(app, "member_a", "PUT", "/api/v1/value/settings", json=BODY)
    assert res.status_code == 403 and res.json()["code"] == "owner_required"


async def test_members_can_read() -> None:
    with _app() as (app, _, _):
        res = await call(app, "member_a", "GET", "/api/v1/value/settings")
    assert res.status_code == 200, res.text


@pytest.mark.parametrize("key", ["admin", "sa"])
async def test_the_operator_has_no_workspace(key: str) -> None:
    with _app() as (app, _, _):
        res = await call(app, key, "GET", "/api/v1/value/settings")
        put = await call(app, key, "PUT", "/api/v1/value/settings", json=BODY)
    for r in (res, put):
        assert r.status_code == 403 and r.json()["code"] == "no_workspace", r.text


async def test_a_knowledge_base_key_is_out_of_scope() -> None:
    with _app() as (app, _, _):
        res = await call(app, "ck_a", "GET", "/api/v1/value/settings")
    assert res.status_code == 403 and res.json()["code"] == "scope_forbidden"


async def test_a_tenant_key_acts_as_an_owner() -> None:
    with _app() as (app, _, store):
        res = await call(app, "tk_a", "PUT", "/api/v1/value/settings", json=BODY)
    assert res.status_code == 200, res.text
    assert store.settings


@pytest.mark.parametrize(
    "patch_body",
    [
        {"lead_value_minor": -1},
        {"minutes_per_answer": 121},
        {"lead_value_minor": 1500.5},
        {"unknown_field": 1},
        {"lead_value_minor": 10**10 + 1},
        {"industry_defaults": "plumber"},
    ],
)
async def test_invalid_settings_are_refused(patch_body: dict[str, Any]) -> None:
    with _app() as (app, _, store):
        res = await call(
            app, "owner_a", "PUT", "/api/v1/value/settings", json={**BODY, **patch_body}
        )
    assert res.status_code == 422, res.text
    assert not store.settings


# ------------------------------------------------------------------ reports


def _now(month: int = 10, day: int = 15) -> datetime:
    return datetime(2026, month, day, 6, 0, tzinfo=UTC)


@contextmanager
def _report_patches(
    store: Store,
    inputs: ValueInputs | None = None,
    frozen: dict[date, dict[str, Any]] | None = None,
    since: date | None = None,
    now: datetime | None = None,
) -> Iterator[None]:
    rows = frozen or {}

    async def frozen_report(_s: Any, _t: Any, month: date) -> Any:
        from callobuzz_rag.api.schemas.value import ValueReportOut

        data = rows.get(month)
        return ValueReportOut.model_validate({**data, "live": False}) if data else None

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return (now or _now()).astimezone(tz) if tz else (now or _now())

    with (
        patch(REPORT_MODULE + "fetch_inputs", AsyncMock(return_value=inputs or _inputs())),
        patch(REPORT_MODULE + "after_hours_since", AsyncMock(return_value=since)),
        patch(REPORT_MODULE + "frozen_report", frozen_report),
        patch("callobuzz_rag.api.routes.value.datetime", FixedDatetime),
        patch(
            "callobuzz_rag.accounts.settings_store.read_engine_settings_row",
            AsyncMock(return_value=(EngineSettings(), None)),
        ),
    ):
        yield


async def test_the_live_report_has_the_numbers_and_the_formula() -> None:
    with _app() as (app, _world, store), _report_patches(store):
        await call(app, "owner_a", "PUT", "/api/v1/value/settings", json=BODY)
        res = await call(app, "owner_a", "GET", "/api/v1/value/report")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["month"] == "2026-10" and body["live"] is True and body["currency"] == "INR"
    assert body["saved_settings"] is True
    b = body["breakdown"]
    assert b["leads_minor"] == 9 * 150_000
    assert b["handoffs_minor"] == 23 * 80_000
    assert b["minutes_saved"] == 1_236 and b["time_minor"] == 1_236 * 25_000 // 60
    assert b["total_minor"] == b["leads_minor"] + b["handoffs_minor"] + b["time_minor"]
    assert [line["key"] for line in b["formula"]] == ["leads", "handoffs", "time"]
    assert body["formula_text"] == [
        "9 qualified leads × ₹1,500 = ₹13,500",
        "23 requests to talk to a person × ₹800 = ₹18,400",
        "412 questions answered × 3 min saved = 20 h 36 min × ₹250/h = ₹5,150",
    ]


async def test_without_saved_settings_the_suggestions_are_used_and_flagged() -> None:
    with _app() as (app, _world, store), _report_patches(store):
        res = await call(app, "owner_a", "GET", "/api/v1/value/report")
    assert res.status_code == 200, res.text
    assert res.json()["saved_settings"] is False
    assert res.json()["settings_used"]["lead_value_minor"] == 150_000


FROZEN = {
    "month": "2026-09",
    "live": False,
    "currency": "INR",
    "saved_settings": True,
    "settings_used": {
        "lead_value_minor": 100000,
        "handoff_value_minor": 50000,
        "staff_hour_minor": 20000,
        "minutes_per_answer": 2,
    },
    "inputs": {
        "conversations": 5,
        "questions": 5,
        "answered": 5,
        "answered_after_hours": 0,
        "leads_total": 1,
        "leads_qualified": 1,
        "leads_partial": 0,
        "handoffs": 0,
        "bookings": 0,
        "median_first_reply_ms": None,
        "top_questions": [],
        "unanswered": [],
    },
    "breakdown": {
        "leads_minor": 100000,
        "handoffs_minor": 0,
        "minutes_saved": 10,
        "time_minor": 3333,
        "total_minor": 103333,
        "formula": [
            {
                "key": "leads",
                "inputs": {"count": 1, "value_minor": 100000},
                "amount_minor": 100000,
                "text": "x",
            },
            {
                "key": "handoffs",
                "inputs": {"count": 0, "value_minor": 50000},
                "amount_minor": 0,
                "text": "y",
            },
            {
                "key": "time",
                "inputs": {"minutes_saved": 10, "staff_hour_minor": 20000},
                "amount_minor": 3333,
                "text": "z",
            },
        ],
    },
    "formula_text": ["x", "y", "z"],
    "after_hours_since": None,
}


async def test_a_frozen_month_does_not_change_when_settings_do() -> None:
    frozen = {date(2026, 9, 1): FROZEN}
    with _app() as (app, _world, store), _report_patches(store, frozen=frozen):
        await call(app, "owner_a", "PUT", "/api/v1/value/settings", json=BODY)
        res = await call(app, "owner_a", "GET", "/api/v1/value/report?month=2026-09")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["live"] is False
    assert body["settings_used"]["lead_value_minor"] == 100000  # not the 150000 saved since
    assert body["breakdown"]["total_minor"] == 103333


@pytest.mark.parametrize("month", ["2026-13", "2027-01", "2026-12", "2020-01"])
async def test_months_outside_the_workspaces_life_are_refused(month: str) -> None:
    with _app() as (app, _world, store), _report_patches(store):
        res = await call(app, "owner_a", "GET", f"/api/v1/value/report?month={month}")
    assert res.status_code == 422, res.text
    assert res.json()["code"] == "value_month_invalid"


async def test_a_malformed_month_is_a_validation_error() -> None:
    with _app() as (app, _world, store), _report_patches(store):
        res = await call(app, "owner_a", "GET", "/api/v1/value/report?month=October")
    assert res.status_code == 422


async def test_the_printable_page_names_the_deployer_and_the_estimate() -> None:
    branding = EngineSettings.model_validate({"branding": {"product_name": "Sahayak AI"}})
    with (
        _app() as (app, _world, store),
        _report_patches(store),
        patch(
            "callobuzz_rag.api.routes.value.read_engine_settings_row",
            AsyncMock(return_value=(branding, None)),
        ),
    ):
        res = await call(app, "owner_a", "GET", "/api/v1/value/report?format=html")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/html")
    assert res.headers["content-security-policy"] == (
        "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:"
    )
    page = res.text
    assert "Sahayak AI" in page
    assert "Estimated value, based on your settings" in page
    assert "ROI" not in page and "guarantee" not in page.lower() and "promise" not in page.lower()


async def test_the_list_of_frozen_months_is_newest_first() -> None:
    rows = [
        SimpleNamespace(
            month=date(2026, 9, 1), data={"currency": "INR", "breakdown": {"total_minor": 5}}
        ),
        SimpleNamespace(
            month=date(2026, 8, 1), data={"currency": "INR", "breakdown": {"total_minor": 7}}
        ),
    ]
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    with _app() as (app, world, _store):
        world.session.execute = AsyncMock(return_value=result)
        res = await call(app, "owner_a", "GET", "/api/v1/value/reports")
    assert res.status_code == 200, res.text
    assert res.json()["items"] == [
        {"month": "2026-09", "total_minor": 5, "currency": "INR"},
        {"month": "2026-08", "total_minor": 7, "currency": "INR"},
    ]


async def test_the_deployer_reads_any_business_and_a_tenant_cannot() -> None:
    with _app() as (app, world, store), _report_patches(store):
        path = f"/api/v1/admin/tenants/{world.tenant_a.id}/value-report"
        admin = await call(app, "admin", "GET", path)
        tenant = await call(app, "owner_a", "GET", path)
        other = await call(app, "tk_b", "GET", path)
        missing = await call(
            app, "admin", "GET", f"/api/v1/admin/tenants/{uuid.uuid4()}/value-report"
        )
    assert admin.status_code == 200, admin.text
    assert admin.json()["breakdown"]["total_minor"] > 0
    assert tenant.status_code == 403 and other.status_code == 403
    assert missing.status_code == 404 and missing.json()["code"] == "tenant_not_found"


async def test_after_hours_counting_start_is_reported_for_earlier_months() -> None:
    with _app() as (app, _world, store), _report_patches(store, since=date(2026, 10, 3)):
        res = await call(app, "owner_a", "GET", "/api/v1/value/report?month=2026-10")
    assert res.json()["after_hours_since"] == "2026-10-03"
