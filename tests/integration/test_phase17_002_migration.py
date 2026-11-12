"""phase17_002 on PostgreSQL (TV5): the wizard's brochure step. Needs Docker (skipped with the
integration suite when down).

- an owner who was on "install" is still on "install" after the upgrade;
- the check allows 1 to 4 and refuses anything else;
- the downgrade folds the steps back and restores the 1 to 3 check.

Sync tests (alembic's env.py calls asyncio.run()).
"""

import asyncio
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import _async_url
from tests.integration.test_accounts_flow import _alembic

pytestmark = pytest.mark.integration


def _sql(url: str, statement: str, **params: Any) -> list[Any]:
    async def go() -> list[Any]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(statement), params)
                return list(result.all()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _workspace(url: str, name: str, step: int) -> Any:
    tenant = _sql(
        url,
        "INSERT INTO tenants (name, api_key_hash, status, plan_id) "
        "SELECT :n, '', 'active', id FROM plans WHERE is_default RETURNING id",
        n=name,
    )[0][0]
    _sql(
        url,
        "INSERT INTO onboarding_states (tenant_id, step, started_by) VALUES (:t, :s, 'a@b.in')",
        t=tenant,
        s=step,
    )
    return tenant


def _steps(url: str, *tenants: Any) -> tuple[int, ...]:
    rows = dict(_sql(url, "SELECT tenant_id, step FROM onboarding_states"))
    return tuple(rows[t] for t in tenants)


def test_steps_shift_for_the_brochure_step() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase17_001")
        # website, install, try
        a, b, c = (_workspace(url, f"Shree Dental {s}", s) for s in (1, 2, 3))
        _alembic(url, "phase17_002")
        assert _steps(url, a, b, c) == (1, 3, 4)  # install stays install
        _sql(url, "UPDATE onboarding_states SET step = 2 WHERE tenant_id = :t", t=a)  # brochures
        _alembic(url, "phase17_001", down=True)
        assert _steps(url, a, b, c) == (2, 2, 3)
        _alembic(url, "phase17_002")
        with pytest.raises(IntegrityError):
            _sql(url, "UPDATE onboarding_states SET step = 5")
        _sql(url, "UPDATE onboarding_states SET step = 4")  # four steps are allowed


def test_the_old_check_is_back_after_a_downgrade() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase17_002")
        tenant = _workspace(url, "Shree Dental Clinic", 4)
        _alembic(url, "phase17_001", down=True)
        assert _steps(url, tenant) == (3,)
        with pytest.raises(IntegrityError):
            _sql(url, "UPDATE onboarding_states SET step = 4")
