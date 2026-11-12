"""P5 T2: one helper decides whether a workspace may use its own AI key.

The lookup runs for real against an in-memory SQLite copy of the plan, workspace and grant
tables (the same approach as `test_tiers_profiles.py`), so the one-query promise and the grant
window are tested, not mocked.
"""

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    DefaultClause,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from callobuzz_rag.billing.byok import byok_allowed, byok_status, usable_headers, usable_llm_config
from callobuzz_rag.db.models.pipeline_profile import PipelineProfile, TenantPipelineSettings
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.user import User

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)

STORED = {
    "provider": "anthropic",
    "model": "anthropic/claude-sonnet-4-5",
    "api_key_encrypted": "gAAAA…",
    "base_url": "https://proxy.example",
    "temperature": 0.2,
    "fast_model": "x",
    "language_models": {"hi": "y"},
}


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element: Any, compiler: Any, **kw: Any) -> str:
    return "JSON"


def test_not_allowed_strips_the_key_and_every_choice_made_for_it() -> None:
    out = usable_llm_config(STORED, allowed=False)
    assert out == {"temperature": 0.2}
    assert STORED["api_key_encrypted"] == "gAAAA…"  # the stored dict is untouched
    assert "model" in STORED and "base_url" in STORED


def test_without_an_own_connection_the_model_choices_stay() -> None:
    picked = {"model": "m", "temperature": 0.2}
    assert usable_llm_config(picked, allowed=False) == picked
    assert usable_llm_config({"model": "m", "base_url": "https://p"}, allowed=False) == {}


def test_allowed_is_an_unchanged_copy() -> None:
    out = usable_llm_config(STORED, allowed=True)
    assert out == STORED and out is not STORED


def test_no_config_stays_none() -> None:
    assert usable_llm_config(None, allowed=False) is None
    assert usable_llm_config(None, allowed=True) is None


def test_headers() -> None:
    h = {
        "x-llm-api-key": "sk-own",
        "x-llm-base-url": "https://p",
        "x-llm-model": "m",
        "x-llm-provider": "openai",
    }
    assert usable_headers(h, allowed=False) == {"x-llm-model": "m", "x-llm-provider": "openai"}
    assert usable_headers(None, allowed=False) == {}
    assert usable_headers(h, allowed=True) == h
    assert usable_headers({"X-LLM-API-Key": "k", "X-LLM-Model": "m"}, allowed=False) == {
        "X-LLM-Model": "m"
    }


@dataclass
class World:
    session: Session
    plans: dict[bool, Plan]
    internal: Plan
    grants: Table
    queries: list[str] = field(default_factory=list)

    @property
    def shim(self) -> Any:
        session = self.session

        class Shim:
            async def execute(self, stmt: Any) -> Any:
                return session.execute(stmt)

        return Shim()

    def tenant(self, *, plan_flag: bool = False, **flags: bool) -> uuid.UUID:
        t = Tenant(name=f"t-{uuid.uuid4().hex[:6]}", plan_id=self.plans[plan_flag].id, **flags)
        self.session.add(t)
        self.session.flush()
        return t.id

    def grant(
        self,
        tenant_id: uuid.UUID,
        *,
        kind: str = "feature_unlock",
        feature: str | None = "byok",
        starts: datetime = NOW - timedelta(days=3),
        ends: datetime | None = NOW + timedelta(days=10),
        revoked: bool = False,
        renews: bool = False,
        cancelled: bool = False,
    ) -> None:
        self.session.execute(
            self.grants.insert().values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                kind=kind,
                feature=feature,
                starts_at=starts,
                ends_at=ends,
                revoked_at=NOW - timedelta(days=1) if revoked else None,
                renews=renews,
                cancelled_at=NOW - timedelta(days=1) if cancelled else None,
            )
        )


@pytest.fixture
def world() -> Iterator[World]:
    engine = create_engine("sqlite://")
    copy = MetaData()
    for model in (User, Plan, Tenant, PipelineProfile, TenantPipelineSettings):
        table = model.__table__.to_metadata(copy)
        for index in list(table.indexes):
            if index.dialect_options["postgresql"]["where"] is not None:
                table.indexes.discard(index)
        for column in table.columns:
            if column.server_default is None:
                continue
            raw = str(getattr(column.server_default, "arg", "")).replace("::jsonb", "")
            raw = raw.replace("now()", "CURRENT_TIMESTAMP")
            if "gen_random_uuid" in raw:
                raw = "(lower(hex(randomblob(16))))"
            column.server_default = DefaultClause(text(raw))
    grants = Table(
        "add_on_grants",
        copy,
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("tenant_id", UUID(as_uuid=True)),
        Column("kind", String),
        Column("feature", String),
        Column("starts_at", DateTime(timezone=True)),
        Column("ends_at", DateTime(timezone=True)),
        Column("revoked_at", DateTime(timezone=True)),
        Column("renews", Boolean, default=False),
        Column("cancelled_at", DateTime(timezone=True)),
    )
    copy.create_all(engine)
    session = Session(engine)
    plans: dict[bool, Plan] = {}
    for flag in (True, False):
        plan = Plan(name=f"plan-{flag}", byok_allowed=flag, is_default=flag)
        session.add(plan)
        plans[flag] = plan
    internal = Plan(name="internal", is_internal=True, byok_allowed=False)
    session.add(internal)
    session.flush()
    w = World(session, plans, internal, grants)

    @event.listens_for(engine, "before_cursor_execute")
    def _count(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            w.queries.append(statement)

    yield w
    session.close()
    engine.dispose()


Setup = Callable[[World, uuid.UUID], None]


@pytest.mark.parametrize(
    ("plan_flag", "setup", "expected", "source"),
    [
        (True, None, True, "plan"),
        (False, None, False, None),
        (False, lambda w, t: w.grant(t), True, "add_on"),
        (False, lambda w, t: w.grant(t, ends=NOW - timedelta(days=1)), False, None),  # ended
        (False, lambda w, t: w.grant(t, revoked=True), False, None),
        (False, lambda w, t: w.grant(t, starts=NOW + timedelta(days=1)), False, None),  # not yet
        (False, lambda w, t: w.grant(t, ends=None), True, "add_on"),  # permanent
        # a renewing grant whose renewal the hourly beat has not run yet is still in force
        (
            False,
            lambda w, t: w.grant(t, ends=NOW - timedelta(minutes=30), renews=True),
            True,
            "add_on",
        ),
        (False, lambda w, t: w.grant(t, ends=NOW - timedelta(hours=3), renews=True), False, None),
        (False, lambda w, t: w.grant(t, ends=NOW - timedelta(minutes=30)), False, None),
        (
            False,
            lambda w, t: w.grant(t, ends=NOW - timedelta(minutes=30), renews=True, cancelled=True),
            False,
            None,
        ),
        (False, lambda w, t: w.grant(t, feature="other"), False, None),
        (False, lambda w, t: w.grant(t, kind="service", feature=None), False, None),
    ],
)
async def test_plan_flag_or_active_byok_grant(
    world: World, plan_flag: bool, setup: Setup | None, expected: bool, source: str | None
) -> None:
    tenant = world.tenant(plan_flag=plan_flag)
    if setup is not None:
        setup(world, tenant)
    assert await byok_allowed(world.shim, tenant, now=NOW) is expected
    assert (await byok_status(world.shim, tenant, now=NOW))[1] == source


async def test_a_grant_of_another_workspace_does_not_count(world: World) -> None:
    mine, other = world.tenant(), world.tenant()
    world.grant(other)
    assert await byok_allowed(world.shim, mine, now=NOW) is False
    assert await byok_allowed(world.shim, other, now=NOW) is True


async def test_exempt_workspaces_are_always_allowed(world: World) -> None:
    operator = world.tenant(is_operator=True)
    system = world.tenant(is_system=True)
    on_internal = Tenant(name="in", plan_id=world.internal.id)
    world.session.add(on_internal)
    world.session.flush()
    for tenant in (operator, system, on_internal.id):
        assert await byok_allowed(world.shim, tenant, now=NOW)


async def test_unknown_workspace_is_allowed(world: World) -> None:
    assert await byok_allowed(world.shim, uuid.uuid4(), now=NOW)


async def test_one_query(world: World) -> None:
    tenant = world.tenant()
    world.grant(tenant)
    world.queries.clear()
    await byok_allowed(world.shim, tenant, now=NOW)
    assert len(world.queries) == 1


async def test_plans_with_the_flag_are_named(world: World) -> None:
    from callobuzz_rag.billing.byok import plans_with_byok

    assert await plans_with_byok(world.shim) == ["plan-True"]  # not the internal or flag-off plans
