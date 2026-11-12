"""Async engine and session factory."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from callobuzz_rag.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.db_url,
        echo=settings.db_echo,
        pool_size=5,
        max_overflow=10,
        # Defence in depth: a connection that died while pooled (a Postgres
        # restart, a cleanup cut short) is replaced on checkout instead of
        # failing the next request with "connection is closed".
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
