"""PIP1: which profile a business answers with.

Runs the real join query against an in-memory SQLite copy of the five tables involved (the
PostgreSQL-only column types are mapped to JSON and the two server functions are registered),
so the resolution order and the one-query promise are tested for real, not mocked.
"""

import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
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

from callobuzz_rag.db.models.pipeline_profile import PipelineProfile, TenantPipelineSettings
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.user import User
from callobuzz_rag.tiers.profiles import (
    downgraded,
    load_profile,
    parse_config,
    plan_profile,
)
from callobuzz_rag.tiers.schema import ProfileConfig
from callobuzz_rag.tiers.seeds import SEEDS


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element: Any, compiler: Any, **kw: Any) -> str:
    return "JSON"


class AsyncShim:
    """The two calls `plan_profile` makes, over a sync SQLite session."""

    def __init__(self, session: Session, counter: list[str]) -> None:
        self._session = session
        self._counter = counter

    async def execute(self, stmt: Any) -> Any:
        return self._session.execute(stmt)


@dataclass
class ProfileDb:
    session: Session
    queries: list[str] = field(default_factory=list)
    profiles: dict[str, PipelineProfile] = field(default_factory=dict)
    plans: dict[str, Plan] = field(default_factory=dict)
    grants: Any = None  # P5 plans

    @property
    def shim(self) -> AsyncShim:
        return AsyncShim(self.session, self.queries)

    def tenant(self, plan: str | None = "standard-plan", **flags: bool) -> uuid.UUID:
        plan_row = self.plans.get(plan) if plan else self.plans["default-plan"]
        t = Tenant(name=f"t-{uuid.uuid4().hex[:6]}", plan_id=plan_row.id, **flags)
        self.session.add(t)
        self.session.flush()
        return t.id

    def override(self, tenant_id: uuid.UUID, key: str | None) -> None:
        self.session.add(
            TenantPipelineSettings(
                tenant_id=tenant_id, profile_id=self.profiles[key].id if key else None
            )
        )
        self.session.flush()


@pytest.fixture
def profile_db() -> Iterator[ProfileDb]:
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _functions(conn: Any, _rec: Any) -> None:
        conn.create_function("now", 0, lambda: "2026-10-05 00:00:00")

    # A copy of the five tables with SQLite-friendly server defaults: the models stay untouched.
    copy = MetaData()
    for model in (User, Plan, Tenant, PipelineProfile, TenantPipelineSettings):
        table = model.__table__.to_metadata(copy)
        for index in list(table.indexes):  # partial unique indexes would turn full on SQLite
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
    # P5 plans: the one column set the own-key lookup reads (the real table has foreign keys to
    # the ledger that this copy does not need)
    db_grants = Table(
        "add_on_grants",
        copy,
        Column("id", UUID(as_uuid=True), primary_key=True),
        Column("tenant_id", UUID(as_uuid=True)),
        Column("kind", String),
        Column("feature", String),
        Column("starts_at", DateTime(timezone=True)),
        Column("ends_at", DateTime(timezone=True)),
        Column("revoked_at", DateTime(timezone=True)),
        Column("renews", Boolean, default=False),  # the renewal grace in byok.active_grant_exists
        Column("cancelled_at", DateTime(timezone=True)),
    )
    # /P5 plans
    copy.create_all(engine)
    session = Session(engine)
    db = ProfileDb(session)
    db.grants = db_grants  # P5 plans
    for key, seed in SEEDS.items():
        row = PipelineProfile(
            id=uuid.uuid4(),
            name=seed["name"],
            label=seed["label"],
            builtin_key=key,
            is_default=seed["is_default"],
            config=seed["config"],
        )
        session.add(row)
        db.profiles[key] = row
    session.flush()
    for name, kwargs in (
        ("default-plan", {"is_default": True}),
        ("premium-plan", {"pipeline_profile_id": db.profiles["premium"].id}),
        ("free-plan", {"pipeline_profile_id": db.profiles["free"].id}),
        ("standard-plan", {}),
        ("internal-plan", {"is_internal": True}),
        ("nokey-plan", {"byok_allowed": False}),  # P5 plans
    ):
        plan = Plan(name=name, **kwargs)
        session.add(plan)
        db.plans[name] = plan
    session.flush()

    @event.listens_for(engine, "before_cursor_execute")
    def _count(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            db.queries.append(statement)

    yield db
    session.close()
    engine.dispose()


async def test_resolution_order(profile_db: ProfileDb) -> None:
    t = profile_db.tenant("premium-plan")
    p = await plan_profile(profile_db.shim, t)  # type: ignore[arg-type]
    assert (p.name, p.source) == ("Premium", "plan")
    assert p.label == "Premium" and p.config.reranker == "llm" and p.config.priority == "high"

    profile_db.override(t, "free")  # a business override beats its plan
    p = await plan_profile(profile_db.shim, t)  # type: ignore[arg-type]
    assert (p.name, p.source) == ("Free", "override")

    t2 = profile_db.tenant("standard-plan")  # plan without a profile: NULL means the default
    p = await plan_profile(profile_db.shim, t2)  # type: ignore[arg-type]
    assert (p.name, p.source) == ("Standard", "default")
    assert p.config == ProfileConfig()  # the no-op overlay

    for flags in ({"is_operator": True}, {"is_system": True}):
        op = profile_db.tenant("standard-plan", **flags)
        p = await plan_profile(profile_db.shim, op)  # type: ignore[arg-type]
        assert (p.name, p.source) == ("Premium", "exempt"), flags
    internal = profile_db.tenant("internal-plan")
    p = await plan_profile(profile_db.shim, internal)  # type: ignore[arg-type]
    assert (p.name, p.source) == ("Premium", "exempt")


async def test_override_row_with_null_profile_follows_the_plan(profile_db: ProfileDb) -> None:
    t = profile_db.tenant("free-plan")
    profile_db.override(t, None)
    p = await plan_profile(profile_db.shim, t)  # type: ignore[arg-type]
    assert (p.name, p.source) == ("Free", "plan")


async def test_unknown_tenant_gets_the_builtin_fallback(profile_db: ProfileDb) -> None:
    p = await plan_profile(profile_db.shim, uuid.uuid4())  # type: ignore[arg-type]
    assert (p.source, p.config) == ("builtin_fallback", ProfileConfig())


async def test_local_profiles_are_unavailable_until_the_local_model_exists(
    profile_db: ProfileDb,
) -> None:
    free = await plan_profile(profile_db.shim, profile_db.tenant("free-plan"))  # type: ignore[arg-type]
    assert free.config.answer_route == "local" and free.available is False
    std = await plan_profile(profile_db.shim, profile_db.tenant("standard-plan"))  # type: ignore[arg-type]
    assert std.available is True


# P5 plans
async def test_profile_carries_the_own_key_allowance(profile_db: ProfileDb) -> None:
    from datetime import UTC, datetime, timedelta

    plain = await plan_profile(profile_db.shim, profile_db.tenant("standard-plan"))  # type: ignore[arg-type]
    assert plain.byok_allowed is True  # a plan that existed before keeps it
    nokey = profile_db.tenant("nokey-plan")
    off = await plan_profile(profile_db.shim, nokey)  # type: ignore[arg-type]
    assert off.byok_allowed is False and off.name == "Standard"  # the profile is unchanged
    now = datetime.now(UTC)
    profile_db.session.execute(
        profile_db.grants.insert().values(
            id=uuid.uuid4(),
            tenant_id=nokey,
            kind="feature_unlock",
            feature="byok",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=5),
        )
    )
    on = await plan_profile(profile_db.shim, nokey)  # type: ignore[arg-type]
    assert on.byok_allowed is True
    for flags in ({"is_operator": True}, {"is_system": True}):
        exempt = profile_db.tenant("nokey-plan", **flags)
        assert (await plan_profile(profile_db.shim, exempt)).byok_allowed is True  # type: ignore[arg-type]


async def test_the_allowance_rides_the_same_single_query(profile_db: ProfileDb) -> None:
    t = profile_db.tenant("nokey-plan")
    profile_db.queries.clear()
    await plan_profile(profile_db.shim, t)  # type: ignore[arg-type]
    assert len(profile_db.queries) == 1


# /P5 plans
async def test_one_query(profile_db: ProfileDb) -> None:
    t = profile_db.tenant("free-plan")
    profile_db.queries.clear()
    await plan_profile(profile_db.shim, t)  # type: ignore[arg-type]
    assert len(profile_db.queries) == 1


async def test_load_profile(profile_db: ProfileDb) -> None:
    free = await load_profile(profile_db.shim, None)  # type: ignore[arg-type]
    assert free is not None and free.name == "Free"
    prem = await load_profile(profile_db.shim, profile_db.profiles["premium"].id)  # type: ignore[arg-type]
    assert prem is not None and prem.name == "Premium"
    assert await load_profile(profile_db.shim, uuid.uuid4()) is None  # type: ignore[arg-type]


def test_stored_bad_key_is_dropped_not_fatal(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        cfg = parse_config(
            {"reranker": "api", "priority": "urgent", "rerank_model": "x/y"}, name="T"
        )
    assert cfg.priority == "normal" and cfg.reranker == "api"
    assert "dropping invalid keys" in caplog.text


def test_stored_config_invalid_as_a_whole_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        cfg = parse_config({"answer_route": "local"}, name="Broken")  # no overrides_kb_settings
    assert cfg == ProfileConfig()
    assert "Broken" in caplog.text
    assert parse_config(None) == ProfileConfig()


def test_downgraded_forces_low_priority() -> None:
    from callobuzz_rag.tiers.profiles import effective

    row = PipelineProfile(
        id=uuid.uuid4(), name="Premium", label="Premium", config={"priority": "high"}
    )
    d = downgraded(effective(row, "plan"))
    assert (d.source, d.config.priority, d.name) == ("downgrade", "low", "Premium")


async def test_a_cold_cache_in_a_worker_is_refreshed_before_a_local_profile_is_judged(
    profile_db: ProfileDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PIP-1: a Celery child has no readiness refresher; the Free profile must not be seen as
    unavailable only because nobody looked yet."""
    import httpx

    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.llm import local as local_mod

    settings = Settings(local_llm_enabled=True, local_llm_model="gemma3:4b")
    local_mod.reset_cache()
    real = local_mod.local_status
    looked: list[bool] = []

    async def via_transport(s: Any, **kw: Any) -> Any:
        looked.append(True)
        transport = httpx.MockTransport(
            lambda r: httpx.Response(200, json={"models": [{"name": "gemma3:4b"}]})
        )
        return await real(s, transport=transport, warm=False)

    monkeypatch.setattr(local_mod, "local_status", via_transport)
    t = profile_db.tenant("free-plan")
    free = await plan_profile(profile_db.shim, t, settings=settings)  # type: ignore[arg-type]
    assert free.config.answer_route == "local" and free.available is True
    assert looked == [True]
    # a profile that answers on the engine's own route never causes a check
    looked.clear()
    std = await plan_profile(  # type: ignore[arg-type]
        profile_db.shim, profile_db.tenant("standard-plan"), settings=settings
    )
    assert std.available is True and looked == []
    # a fresh cache is not asked again
    await plan_profile(profile_db.shim, t, settings=settings)  # type: ignore[arg-type]
    assert looked == []
    local_mod.reset_cache()
