"""Integration test fixtures with testcontainers.

Requires Docker to be running. Tests marked with @pytest.mark.integration.

Note on the Alembic fixture below: ``alembic/env.py`` runs migrations through
``asyncio.run()``, so ``command.upgrade`` must be called from a thread that has no
running event loop. Calling it from inside an ``async`` fixture raises
``RuntimeError: asyncio.run() cannot be called from a running event loop`` and takes
every integration test down at setup. That is why ``migrated_postgres`` is a plain
``def`` fixture: pytest sets sync fixtures up outside the loop pytest-asyncio runs
the test in.
"""

import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

RAG_ROOT = Path(__file__).resolve().parents[2]
# The queue and cache store the engine ships (Valkey, a Redis-compatible store); one place to bump it.
VALKEY_IMAGE = "valkey/valkey:8.1-alpine"


async def new_tenant_id(session) -> uuid.UUID:  # type: ignore[no-untyped-def]
    """A fresh tenant, flushed in `session`: every corpus belongs to one (accounts A1)."""
    from callobuzz_rag.db.models.tenant import Tenant

    tenant = Tenant(name=f"test-{uuid.uuid4().hex[:8]}")
    session.add(tenant)
    await session.flush()
    return tenant.id


def _async_url(container: PostgresContainer) -> str:
    """testcontainers hands back a psycopg2 URL; alembic/env.py and our engine are async."""
    url = container.get_connection_url()
    # `localhost` resolves to ::1 first on Windows, and Docker Desktop resets that
    # connection (WinError 64) before asyncpg falls back to IPv4.
    url = url.replace("@localhost:", "@127.0.0.1:")
    return url.replace("+psycopg2", "+asyncpg").replace("postgresql://", "postgresql+asyncpg://")


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def redis_container():
    with RedisContainer(VALKEY_IMAGE) as redis:
        yield redis


@pytest.fixture(scope="session")
def qdrant_container():
    container = DockerContainer("qdrant/qdrant:v1.13.2")
    container.with_exposed_ports(6333)
    container.start()
    wait_for_logs(container, "Qdrant HTTP listening", timeout=30)
    yield container
    container.stop()


@pytest.fixture(scope="session")
def migrated_postgres(postgres_container: PostgresContainer) -> PostgresContainer:
    """Apply the full Alembic history once per session.

    Deliberately a sync fixture — see the module docstring.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(RAG_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAG_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _async_url(postgres_container))
    command.upgrade(cfg, "head")
    return postgres_container


@pytest_asyncio.fixture
async def db_engine(migrated_postgres: PostgresContainer):
    engine = create_async_engine(_async_url(migrated_postgres))
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
