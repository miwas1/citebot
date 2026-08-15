"""Primary-database LangGraph checkpoint saver for review gates."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from sqlalchemy import delete, select

from app.db.models import LangGraphCheckpointRecord
from app.db.session import DatabaseSessionManager


class DatabaseCheckpointSaver(BaseCheckpointSaver):
    """Persist async LangGraph checkpoints in CiteBot's primary database."""

    def __init__(self, session_manager: DatabaseSessionManager) -> None:
        super().__init__()
        self._session_manager = session_manager

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Reject sync use because the application uses an async database engine."""

        raise NotImplementedError("Use aput with CiteBot's async database")

    async def aput(
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
        async with self._session_manager.session() as session:
            record = await session.get(LangGraphCheckpointRecord, checkpoint_id)
            if record is None:
                record = LangGraphCheckpointRecord(checkpoint_id=checkpoint_id)
                session.add(record)
            record.thread_id = str(values["thread_id"])
            record.checkpoint_ns = str(values["checkpoint_ns"])
            record.parent_checkpoint_id = values.get("checkpoint_id")
            record.checkpoint_type = checkpoint_type
            record.checkpoint_blob = checkpoint_blob
            record.metadata_type = metadata_type
            record.metadata_blob = metadata_blob
        return _continuation_config(values, checkpoint_id)

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Reject sync use because the application uses an async database engine."""

        raise NotImplementedError("Use aget_tuple with CiteBot's async database")

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """Load an exact checkpoint or the latest checkpoint for a thread."""

        values = _config_values(config)
        statement = select(LangGraphCheckpointRecord)
        if values.get("checkpoint_id"):
            statement = statement.where(
                LangGraphCheckpointRecord.checkpoint_id == values["checkpoint_id"]
            )
        else:
            statement = statement.where(
                LangGraphCheckpointRecord.thread_id == values["thread_id"],
                LangGraphCheckpointRecord.checkpoint_ns == values["checkpoint_ns"],
            ).order_by(LangGraphCheckpointRecord.created_at.desc())
        async with self._session_manager.session() as session:
            record = await session.scalar(statement.limit(1))
        return self._to_tuple(record) if record else None

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        """Reject sync use because the application uses an async database engine."""

        raise NotImplementedError("Use alist with CiteBot's async database")

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """List checkpoints for one thread in reverse creation order."""

        statement = select(LangGraphCheckpointRecord)
        if config:
            values = _config_values(config)
            statement = statement.where(
                LangGraphCheckpointRecord.thread_id == values["thread_id"],
                LangGraphCheckpointRecord.checkpoint_ns == values["checkpoint_ns"],
            )
        statement = statement.order_by(LangGraphCheckpointRecord.created_at.desc())
        if limit is not None:
            statement = statement.limit(limit)
        async with self._session_manager.session() as session:
            records = (await session.scalars(statement)).all()
        for record in records:
            yield self._to_tuple(record)

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Reject sync use because the application uses an async database engine."""

        raise NotImplementedError("Use aput_writes with CiteBot's async database")

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Keep interrupt writes bounded; the checkpoint remains authoritative."""

    def delete_thread(self, thread_id: str) -> None:
        """Reject sync use because the application uses an async database engine."""

        raise NotImplementedError("Use adelete_thread with CiteBot's async database")

    async def adelete_thread(self, thread_id: str) -> None:
        """Delete all checkpoints for one completed review thread."""

        async with self._session_manager.session() as session:
            await session.execute(
                delete(LangGraphCheckpointRecord).where(
                    LangGraphCheckpointRecord.thread_id == thread_id
                )
            )

    def _to_tuple(self, record: LangGraphCheckpointRecord) -> CheckpointTuple:
        checkpoint = self.serde.loads_typed(
            (record.checkpoint_type, record.checkpoint_blob)
        )
        metadata = self.serde.loads_typed((record.metadata_type, record.metadata_blob))
        config = _continuation_config(
            {
                "thread_id": record.thread_id,
                "checkpoint_ns": record.checkpoint_ns,
            },
            record.checkpoint_id,
        )
        parent = (
            _continuation_config(
                {
                    "thread_id": record.thread_id,
                    "checkpoint_ns": record.checkpoint_ns,
                },
                record.parent_checkpoint_id,
            )
            if record.parent_checkpoint_id
            else None
        )
        return CheckpointTuple(config, checkpoint, metadata, parent)


def _config_values(config: RunnableConfig | None) -> dict[str, str | None]:
    """Extract stable configurable identifiers from a LangGraph config."""

    configurable = (config or {}).get("configurable", {})
    return {
        "thread_id": str(configurable.get("thread_id", "default")),
        "checkpoint_ns": str(configurable.get("checkpoint_ns", "")),
        "checkpoint_id": configurable.get("checkpoint_id"),
    }


def _continuation_config(
    values: dict[str, str | None], checkpoint_id: str
) -> RunnableConfig:
    """Build the config required to resume the stored thread."""

    return {
        "configurable": {
            "thread_id": values["thread_id"],
            "checkpoint_ns": values["checkpoint_ns"],
            "checkpoint_id": checkpoint_id,
        }
    }
