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

    def __init__(self, database_url: str, sqlite_busy_timeout_ms: int = 5000) -> None:
        """Build the async engine and session factory for the configured database."""

        self._engine = create_async_engine(
            database_url, future=True, pool_pre_ping=True
        )
        self._sqlite_busy_timeout_ms = sqlite_busy_timeout_ms
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def initialize(self) -> None:
        """Create metadata tables used by the application."""

        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            if connection.dialect.name == "sqlite":
                await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
                await connection.exec_driver_sql(
                    f"PRAGMA busy_timeout={self._sqlite_busy_timeout_ms}"
                )
                await connection.run_sync(self._ensure_sqlite_columns)
            await connection.run_sync(run_migrations)

    def _ensure_sqlite_columns(self, connection) -> None:
        """Apply additive columns for installations created before structured data."""

        additions = {
            "documents": {
                "project_id": "VARCHAR(64)",
            },
            "research_sessions": {
                "project_id": "VARCHAR(64)",
            },
            "analysis_runs": {
                "project_id": "VARCHAR(64)",
            },
            "chunks": {
                "element_ids": "JSON",
                "bbox_refs": "JSON",
                "extraction_method": "VARCHAR(64)",
                "min_confidence": "FLOAT",
                "parent_chunk_id": "VARCHAR(64)",
                "chunk_level": "VARCHAR(32) DEFAULT 'window'",
                "heading_path": "JSON",
                "content_hash": "VARCHAR(64)",
                "version_id": "VARCHAR(64)",
                "is_current": "BOOLEAN DEFAULT 1",
                "ordinal": "INTEGER DEFAULT 0",
                "source_anchor_ids": "JSON",
            },
            "ingestion_jobs": {
                "project_id": "VARCHAR(64)",
                "attempt_count": "INTEGER DEFAULT 0",
                "max_attempts": "INTEGER DEFAULT 3",
                "lease_owner": "VARCHAR(128)",
                "lease_expires_at": "DATETIME",
                "heartbeat_at": "DATETIME",
                "stage": "VARCHAR(64)",
                "progress_current": "INTEGER DEFAULT 0",
                "progress_total": "INTEGER DEFAULT 0",
            },
        }
        from sqlalchemy import inspect

        inspector = inspect(connection)
        for table_name, columns in additions.items():
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, column_type in columns.items():
                if column_name not in existing:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
                    )

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
