"""GET /api/v1/admin/audit (accounts A8): who may read it, how filters become SQL, and
which parameters are refused. `tests/integration/test_audit_api_db.py` runs the same
filters against PostgreSQL rows."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from callobuzz_rag.accounts.audit import AUDIT_ACTIONS, RETIRED_AUDIT_ACTIONS
from callobuzz_rag.api.routes.audit import ACTION_PREFIXES
from callobuzz_rag.db.models.audit_log import AuditLog
from tests.unit.scope_harness import KEYS, World, call, scoped_app

PATH = "/api/v1/admin/audit"


def _row(**over: Any) -> AuditLog:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "created_at": datetime(2026, 9, 30, 12, tzinfo=UTC),
        "actor_kind": "admin_key",
        "actor_user_id": None,
        "actor_label": "admin key",
        "action": "tenant.activated",
        "tenant_id": uuid.uuid4(),
        "tenant_label": "Shree Labs",
        "target_type": "tenant",
        "target_id": "t",
        "details": {"note": "paid"},
        "ip": "203.0.113.9",
        **over,
    }
    return AuditLog(**values)


class _Capture:
    """Answers the count and the page query, keeping the SQL each one ran."""

    def __init__(self, rows: list[AuditLog], total: int | None = None) -> None:
        self.rows = rows
        self.total = len(rows) if total is None else total
        self.sql: list[str] = []

    async def execute(self, stmt: Any) -> Any:
        self.sql.append(
            str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        )
        result = MagicMock()
        result.scalar_one.return_value = self.total
        result.scalars.return_value.all.return_value = self.rows
        return result


async def _get(key: str | None, capture: _Capture | None = None, **params: Any) -> Any:
    world = World()
    if capture is not None:
        world.session.execute = AsyncMock(side_effect=capture.execute)
    with scoped_app(world) as (app, _):
        return await call(app, key, "GET", PATH, params=params)


@pytest.mark.parametrize("key", [k for k in KEYS if k not in ("admin", "sa")])
async def test_only_the_operator_reads_it(key: str) -> None:
    res = await _get(key)
    assert res.status_code == 403, res.text


async def test_no_key_is_401() -> None:
    res = await _get(None)
    assert res.status_code == 401


@pytest.mark.parametrize("key", ["admin", "sa"])
async def test_shape_newest_first_default_window(key: str) -> None:
    rows = [_row(), _row(action="tenant.signed_up", actor_kind="user", actor_label="a@b.example")]
    capture = _Capture(rows, total=7)
    res = await _get(key, capture)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 7 and body["offset"] == 0 and body["limit"] == 50
    assert [i["action"] for i in body["items"]] == ["tenant.activated", "tenant.signed_up"]
    assert set(body["items"][0]) == {
        "id",
        "created_at",
        "actor_kind",
        "actor_user_id",
        "actor_label",
        "action",
        "tenant_id",
        "tenant_label",
        "target_type",
        "target_id",
        "details",
        "ip",
    }
    count_sql, page_sql = capture.sql
    assert "count(*)" in count_sql
    assert "ORDER BY audit_log.created_at DESC, audit_log.id DESC" in page_sql
    assert "LIMIT 50 OFFSET 0" in page_sql
    # the default window: the last 90 days
    since = (datetime.now(UTC) - timedelta(days=90)).date().isoformat()
    assert f"audit_log.created_at >= '{since}" in page_sql


async def test_filters_become_the_where_clause() -> None:
    tenant = uuid.uuid4()
    capture = _Capture([])
    res = await _get(
        "admin",
        capture,
        tenant_id=str(tenant),
        action="member.invited",
        actor="ASHA_%",
        start="2026-09-01T00:00:00",
        end="2026-09-30T00:00:00+05:30",
        offset=10,
        limit=200,
    )
    assert res.status_code == 200, res.text
    sql = capture.sql[1]
    assert f"audit_log.tenant_id = '{tenant}'" in sql
    assert "audit_log.action = 'member.invited'" in sql
    # a substring, case-insensitive, with LIKE's wildcards taken literally
    # (the compiler doubles % for the driver's paramstyle)
    assert "audit_log.actor_label ILIKE '%%' || 'ASHA/_/%%' || '%%' ESCAPE '/'" in sql
    assert "audit_log.created_at >= '2026-09-01 00:00:00+00:00'" in sql  # naive = UTC
    assert "audit_log.created_at <= '2026-09-29 18:30:00+00:00'" in sql  # converted to UTC
    assert "LIMIT 200 OFFSET 10" in sql


async def test_action_prefix() -> None:
    capture = _Capture([])
    res = await _get("admin", capture, action="tenant.")
    assert res.status_code == 200, res.text
    assert "audit_log.action LIKE 'tenant.' || '%%' ESCAPE '/'" in capture.sql[1]


def test_prefixes_come_from_the_catalog() -> None:
    assert {"tenant.", "member.", "plan.", "user.", "session.", "settings."} <= ACTION_PREFIXES
    known = AUDIT_ACTIONS | RETIRED_AUDIT_ACTIONS
    assert all(any(a.startswith(p) for a in known) for p in ACTION_PREFIXES)
    assert all(p.endswith(".") for p in ACTION_PREFIXES)


@pytest.mark.parametrize(
    "params",
    [
        {"action": "tenant.nuked"},
        {"action": "tenant"},  # a prefix must end in "."
        {"action": "tenant.%"},
        {"action": "nothing."},
        {"start": "2025-01-01T00:00:00Z", "end": "2026-01-03T00:00:00Z"},  # 367 days
        {"start": "2026-09-30T00:00:00Z", "end": "2026-09-01T00:00:00Z"},  # reversed
        {"start": "not a date"},
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"tenant_id": "nope"},
    ],
)
async def test_bad_parameters_are_422(params: dict[str, Any]) -> None:
    res = await _get("admin", _Capture([]), **params)
    assert res.status_code == 422, res.text


async def test_a_full_year_is_allowed() -> None:
    res = await _get(
        "admin", _Capture([]), start="2025-01-01T00:00:00Z", end="2026-01-02T00:00:00Z"
    )
    assert res.status_code == 200, res.text


async def test_only_start_gives_ninety_days_after_it() -> None:
    capture = _Capture([])
    res = await _get("admin", capture, start="2026-01-01T00:00:00Z")
    assert res.status_code == 200, res.text
    assert "audit_log.created_at <= '2026-04-01 00:00:00+00:00'" in capture.sql[1]
