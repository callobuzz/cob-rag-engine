"""The operator workspace on a real PostgreSQL: migration phase11_009 up and down, the
partial unique indexes, and the bootstrap run twice (and for a new super admin).

Sync tests: alembic's env.py calls asyncio.run(). Requires Docker.
"""

import asyncio
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from callobuzz_rag.accounts import bootstrap
from callobuzz_rag.core.config import Settings
from tests.integration.conftest import _async_url
from tests.integration.test_accounts_flow import _alembic

pytestmark = pytest.mark.integration

SECRET = "i" * 48
EMAIL = "ops@operator.example"


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


def _bootstrap(url: str, email: str = EMAIL, **kw: Any) -> int:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        session_secret=SECRET,  # type: ignore[arg-type]
        superadmin_email=email,
        email_provider="log",
    )

    async def go() -> int:
        engine = create_async_engine(url)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            return await bootstrap.run(settings, factory, out=lambda _line: None, **kw)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _limits_of_operator(url: str) -> Any:
    from callobuzz_rag.accounts import caps

    async def go() -> Any:
        engine = create_async_engine(url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                tenant_id = (
                    await session.execute(text("SELECT id FROM tenants WHERE is_operator"))
                ).scalar_one()
                limits = await caps.effective_limits(session, tenant_id)
                usage = await caps.usage_snapshot(session, tenant_id)
                return limits, usage
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_migration_seeds_the_plans_and_downgrades_cleanly() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase11_008")
        _sql(
            url,
            "INSERT INTO tenants (name, api_key_hash, plan_id) "
            "VALUES ('Shree', '', (SELECT id FROM plans WHERE is_default))",
        )

        _alembic(url, "phase11_009")
        plans = _sql(
            url,
            "SELECT name, is_internal, is_default, is_builtin, max_knowledge_bases, "
            "monthly_questions, max_documents, max_widget_keys FROM plans ORDER BY name",
        )
        assert plans == [
            ("Internal", True, False, False, None, None, None, None),
            ("Pilot", False, False, False, 1, 300, 50, 1),
            ("Standard", False, True, True, None, None, None, None),
        ]
        assert _sql(url, "SELECT is_operator FROM tenants") == [(False,)]
        assert _sql(url, "SELECT operator_workspace_removed FROM engine_settings") == [(False,)]
        with pytest.raises(IntegrityError, match="uq_plans_internal"):
            _sql(url, "UPDATE plans SET is_internal = true WHERE name = 'Pilot'")
        _sql(url, "UPDATE tenants SET is_operator = true")
        with pytest.raises(IntegrityError, match="uq_tenants_operator"):
            _sql(
                url,
                "INSERT INTO tenants (name, api_key_hash, is_operator, plan_id) "
                "VALUES ('B', '', true, (SELECT id FROM plans WHERE is_default))",
            )

        # A tenant on Internal moves to the default plan on the way down.
        _sql(url, "UPDATE tenants SET plan_id = (SELECT id FROM plans WHERE is_internal)")
        _alembic(url, "phase11_008", down=True)
        assert _sql(url, "SELECT name FROM plans ORDER BY name") == [("Pilot",), ("Standard",)]
        assert _sql(url, "SELECT p.name FROM tenants t JOIN plans p ON p.id = t.plan_id") == [
            ("Standard",)
        ]
        for table, column in (
            ("tenants", "is_operator"),
            ("plans", "is_internal"),
            ("engine_settings", "operator_workspace_removed"),
        ):
            assert (
                _sql(
                    url,
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = :c",
                    t=table,
                    c=column,
                )
                == []
            )
        _alembic(url, "head")  # and up again: the seed is repeatable
        assert len(_sql(url, "SELECT 1 FROM plans WHERE is_internal")) == 1


def test_migration_keeps_an_operators_own_pilot_and_internal_names() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase11_008")
        _sql(
            url,
            "INSERT INTO plans (name, max_documents) VALUES ('Pilot', 7), ('Internal', 9)",
        )
        _alembic(url, "phase11_009")
        rows = _sql(url, "SELECT name, is_internal, max_documents FROM plans ORDER BY name")
        assert rows == [
            ("Internal", False, 9),  # theirs, untouched
            ("Internal (operator)", True, None),  # the seeded one, renamed to fit
            ("Pilot", False, 7),  # theirs: not overwritten
            ("Standard", False, None),
        ]


def test_bootstrap_twice_and_for_a_new_super_admin() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")
        _sql(
            url,
            "INSERT INTO tenants (name, api_key_hash, plan_id) "
            "VALUES ('Shree', '', (SELECT id FROM plans WHERE is_default))",
        )

        assert _bootstrap(url) == 0
        assert _bootstrap(url) == 0
        rows = _sql(
            url,
            "SELECT t.name, t.status, p.name, p.is_internal, t.is_operator FROM tenants t "
            "JOIN plans p ON p.id = t.plan_id WHERE t.is_operator",
        )
        assert rows == [("My workspace", "active", "Internal", True, True)]
        limits, _usage = _limits_of_operator(url)  # the Internal plan is unlimited everywhere
        assert (limits.max_knowledge_bases, limits.monthly_questions) == (None, None)
        assert (limits.max_documents, limits.max_widget_keys) == (None, None)
        owners = _sql(
            url,
            "SELECT u.email, m.role FROM tenant_members m JOIN users u ON u.id = m.user_id "
            "JOIN tenants t ON t.id = m.tenant_id WHERE t.is_operator",
        )
        assert owners == [(EMAIL, "owner")]
        assert _sql(
            url, "SELECT count(*) FROM audit_log WHERE action = :a", a="operator_workspace.created"
        ) == [(1,)]

        # a new person becomes the super admin: same workspace, new owner, old one out
        _sql(url, "UPDATE users SET password_hash = 'h' WHERE email = :e", e=EMAIL)
        assert _bootstrap(url, "new-ops@operator.example") == 0
        assert _sql(url, "SELECT count(*) FROM tenants WHERE is_operator") == [(1,)]
        owners = _sql(
            url,
            "SELECT u.email FROM tenant_members m JOIN users u ON u.id = m.user_id "
            "JOIN tenants t ON t.id = m.tenant_id WHERE t.is_operator",
        )
        assert owners == [("new-ops@operator.example",)]

        # deleted on purpose: stays deleted; --restore-operator-workspace brings it back
        _sql(url, "DELETE FROM tenants WHERE is_operator")
        _sql(url, "UPDATE engine_settings SET operator_workspace_removed = true")
        assert _bootstrap(url, "new-ops@operator.example") == 0
        assert _sql(url, "SELECT count(*) FROM tenants WHERE is_operator") == [(0,)]
        assert _bootstrap(url, "new-ops@operator.example", restore_operator_workspace=True) == 0
        assert _sql(url, "SELECT count(*) FROM tenants WHERE is_operator") == [(1,)]
        assert _sql(url, "SELECT operator_workspace_removed FROM engine_settings") == [(False,)]


def test_the_branding_name_and_the_no_address_case() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")
        _sql(
            url,
            "UPDATE engine_settings SET value = CAST(:v AS jsonb) WHERE id = 1",
            v='{"branding": {"product_name": "Sahayak AI"}}',
        )
        assert _bootstrap(url, "") == 0  # no super-admin address: an empty workspace
        assert _sql(url, "SELECT name FROM tenants WHERE is_operator") == [("Sahayak AI",)]
        assert _sql(
            url,
            "SELECT count(*) FROM tenant_members m JOIN tenants t ON t.id = m.tenant_id "
            "WHERE t.is_operator",
        ) == [(0,)]
        assert _bootstrap(url, "") == 0
        assert _sql(url, "SELECT count(*) FROM tenants WHERE is_operator") == [(1,)]
