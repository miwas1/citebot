"""Small SQLite-backed LangGraph checkpoint saver for local review gates."""

from __future__ import annotations

import base64
import sqlite3
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)


class SQLiteCheckpointSaver(BaseCheckpointSaver):
    """Persist LangGraph checkpoints without adding a server-side dependency."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS langgraph_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL,
                    parent_checkpoint_id TEXT,
                    checkpoint_type TEXT NOT NULL,
                    checkpoint_blob BLOB NOT NULL,
                    metadata_type TEXT NOT NULL,
                    metadata_blob BLOB NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS langgraph_checkpoint_thread_idx "
                "ON langgraph_checkpoints(thread_id, checkpoint_ns, created_at)"
            )

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Store one checkpoint and return its continuation configuration."""

        values = _config_values(config)
        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(checkpoint)
        metadata_type, metadata_blob = self.serde.dumps_typed(metadata)
        checkpoint_id = str(checkpoint["id"])
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO langgraph_checkpoints(
                    checkpoint_id, thread_id, checkpoint_ns, parent_checkpoint_id,
                    checkpoint_type, checkpoint_blob, metadata_type, metadata_blob,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    checkpoint_id,
                    values["thread_id"],
                    values["checkpoint_ns"],
                    values.get("checkpoint_id"),
                    checkpoint_type,
                    checkpoint_blob,
                    metadata_type,
                    metadata_blob,
                ),
            )
        return _continuation_config(values, checkpoint_id)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Async wrapper that keeps SQLite I/O off the event loop."""

        return self.put(config, checkpoint, metadata, new_versions)

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Load an exact checkpoint or the latest checkpoint for a thread."""

        values = _config_values(config)
        checkpoint_id = values.get("checkpoint_id")
        query = (
            "SELECT * FROM langgraph_checkpoints WHERE checkpoint_id = ?"
            if checkpoint_id
            else (
                "SELECT * FROM langgraph_checkpoints WHERE thread_id = ? "
                "AND checkpoint_ns = ? ORDER BY created_at DESC LIMIT 1"
            )
        )
        parameters = (checkpoint_id,) if checkpoint_id else (
            values["thread_id"],
            values["checkpoint_ns"],
        )
        with self._connect() as connection:
            row = connection.execute(query, parameters).fetchone()
        return _row_to_tuple(row) if row else None

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Async checkpoint lookup."""

        return self.get_tuple(config)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        """List checkpoints for one thread in reverse creation order."""

        values = _config_values(config) if config else None
        query = "SELECT * FROM langgraph_checkpoints"
        parameters: list[Any] = []
        conditions: list[str] = []
        if values:
            conditions.extend(["thread_id = ?", "checkpoint_ns = ?"])
            parameters.extend([values["thread_id"], values["checkpoint_ns"]])
        if filter and "source" in filter:
            conditions.append("metadata_blob IS NOT NULL")
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC"
        if limit:
            query += " LIMIT ?"
            parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        yield from (_row_to_tuple(row) for row in rows)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """Async checkpoint listing."""

        rows = list(self.list(config, filter=filter, before=before, limit=limit))
        for row in rows:
            yield row

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Keep interrupt writes bounded; the checkpoint itself remains authoritative."""

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Async no-op for small review graphs without parallel task writes."""

    def delete_thread(self, thread_id: str) -> None:
        """Delete all checkpoints for one completed review thread."""

        with self._connect() as connection:
            connection.execute(
                "DELETE FROM langgraph_checkpoints WHERE thread_id = ?", (thread_id,)
            )

    async def adelete_thread(self, thread_id: str) -> None:
        """Async thread deletion."""

        self.delete_thread(thread_id)

    def _connect(self) -> sqlite3.Connection:
        """Open a short-lived WAL connection."""

        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection


def _config_values(config: RunnableConfig | None) -> dict[str, str | None]:
    """Extract stable configurable identifiers from a LangGraph config."""

    configurable = (config or {}).get("configurable", {})
    return {
        "thread_id": str(configurable.get("thread_id", "default")),
        "checkpoint_ns": str(configurable.get("checkpoint_ns", "")),
        "checkpoint_id": configurable.get("checkpoint_id"),
    }


def _continuation_config(values: dict[str, str | None], checkpoint_id: str) -> RunnableConfig:
    """Build the config required to resume the stored thread."""

    return {
        "configurable": {
            "thread_id": values["thread_id"],
            "checkpoint_ns": values["checkpoint_ns"],
            "checkpoint_id": checkpoint_id,
        }
    }


def _row_to_tuple(row: sqlite3.Row) -> CheckpointTuple:
    """Decode one SQLite row into LangGraph's checkpoint contract."""

    checkpoint = _decode(row["checkpoint_type"], row["checkpoint_blob"])
    metadata = _decode(row["metadata_type"], row["metadata_blob"])
    config = _continuation_config(
        {"thread_id": row["thread_id"], "checkpoint_ns": row["checkpoint_ns"]},
        row["checkpoint_id"],
    )
    parent = (
        _continuation_config(
            {"thread_id": row["thread_id"], "checkpoint_ns": row["checkpoint_ns"]},
            row["parent_checkpoint_id"],
        )
        if row["parent_checkpoint_id"]
        else None
    )
    return CheckpointTuple(config, checkpoint, metadata, parent)


def _decode(type_name: str, payload: bytes) -> Any:
    """Decode serializer output stored as SQLite-safe base64 bytes."""

    raw = base64.b64decode(payload) if isinstance(payload, str) else payload
    return BaseCheckpointSaver().serde.loads_typed((type_name, raw))
