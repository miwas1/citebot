"""Async database engine and session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.migrations import run_migrations


class DatabaseSessionManager:
    """Create, initialize, and dispose async database sessions."""

    def __init__(self, database_url: str) -> None:
        """Build the async engine and session factory for the configured database."""

        self._engine = create_async_engine(
            database_url, future=True, pool_pre_ping=True
        )
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    @property
    def dialect_name(self) -> str:
        """Return the configured SQL dialect name."""

        return self._engine.dialect.name

    @property
    def is_postgresql(self) -> bool:
        """Return whether this manager is connected to PostgreSQL."""

        return self.dialect_name == "postgresql"

    async def initialize(self) -> None:
        """Create metadata tables used by the application."""

        async with self._engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                # API and worker start together; serialize their schema bootstrap.
                await connection.execute(
                    text("SELECT pg_advisory_xact_lock(734867320240815001)")
                )
            await connection.run_sync(Base.metadata.create_all)
            await connection.run_sync(run_migrations)

    async def ping(self) -> bool:
        """Check whether the database accepts a trivial query."""

        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a managed async session with commit or rollback handling."""

        session = self._session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def close(self) -> None:
        """Dispose the database engine and release pooled resources."""

        await self._engine.dispose()
