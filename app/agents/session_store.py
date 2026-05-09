"""Database-backed session persistence for replayable research conversations."""

from __future__ import annotations

from sqlalchemy import select

from app.agents.schemas import ResearchSessionRecord
from app.db.models import ResearchSessionRecordModel
from app.db.session import DatabaseSessionManager


class ResearchSessionStore:
    """Store structured research session state with durable database persistence."""

    def __init__(self, session_manager: DatabaseSessionManager) -> None:
        """Store the session manager used to load and save records."""

        self._session_manager = session_manager

    async def get(self, session_id: str) -> ResearchSessionRecord | None:
        """Return the persisted session record when one exists."""

        async with self._session_manager.session() as session:
            record = await session.get(ResearchSessionRecordModel, session_id)
        if record is None:
            return None
        return ResearchSessionRecord.model_validate(
            {
                "session_id": record.session_id,
                "turns": record.turns_json,
                "memory": record.memory_json,
                "last_trace_id": record.last_trace_id,
            }
        )

    async def save(self, record: ResearchSessionRecord) -> None:
        """Persist the latest session record by its identifier."""

        payload = record.model_dump(mode="json")
        async with self._session_manager.session() as session:
            existing = await session.get(ResearchSessionRecordModel, record.session_id)
            if existing is None:
                session.add(
                    ResearchSessionRecordModel(
                        session_id=record.session_id,
                        turns_json=payload["turns"],
                        memory_json=payload["memory"],
                        last_trace_id=record.last_trace_id,
                    )
                )
                return
            existing.turns_json = payload["turns"]
            existing.memory_json = payload["memory"]
            existing.last_trace_id = record.last_trace_id
