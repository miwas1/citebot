"""Database-backed session persistence for replayable research conversations."""

from __future__ import annotations

from sqlalchemy import delete, select

from app.agents.schemas import DEFAULT_PROJECT_ID, ConversationSummary, ResearchSessionRecord
from app.db.models import ResearchSessionRecordModel
from app.db.session import DatabaseSessionManager


class ResearchSessionStore:
    """Store structured research session state with durable database persistence."""

    def __init__(self, session_manager: DatabaseSessionManager) -> None:
        """Store the session manager used to load and save records."""

        self._session_manager = session_manager

    async def get(
        self, session_id: str, project_id: str = DEFAULT_PROJECT_ID
    ) -> ResearchSessionRecord | None:
        """Return the persisted session record when one exists."""

        async with self._session_manager.session() as session:
            record = await session.scalar(
                select(ResearchSessionRecordModel).where(
                    ResearchSessionRecordModel.session_id == session_id,
                    ResearchSessionRecordModel.project_id == project_id,
                )
            )
        if record is None:
            return None
        return ResearchSessionRecord.model_validate(
            {
                "session_id": record.session_id,
                "project_id": record.project_id or DEFAULT_PROJECT_ID,
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
                        project_id=record.project_id,
                        turns_json=payload["turns"],
                        memory_json=payload["memory"],
                        last_trace_id=record.last_trace_id,
                    )
                )
                return
            if (existing.project_id or DEFAULT_PROJECT_ID) != record.project_id:
                raise ValueError("Conversation belongs to another project")
            existing.turns_json = payload["turns"]
            existing.memory_json = payload["memory"]
            existing.last_trace_id = record.last_trace_id

    async def belongs_to_other_project(self, session_id: str, project_id: str) -> bool:
        """Return whether a session identifier is owned by another project."""

        async with self._session_manager.session() as session:
            existing = await session.get(ResearchSessionRecordModel, session_id)
        return bool(existing and (existing.project_id or DEFAULT_PROJECT_ID) != project_id)

    async def list(
        self, limit: int = 100, project_id: str = DEFAULT_PROJECT_ID
    ) -> list[ConversationSummary]:
        """Return recently updated conversation summaries."""

        async with self._session_manager.session() as session:
            records = (
                await session.scalars(
                    select(ResearchSessionRecordModel)
                    .where(ResearchSessionRecordModel.project_id == project_id)
                    .order_by(ResearchSessionRecordModel.updated_at.desc())
                    .limit(limit)
                )
            ).all()
        summaries: list[ConversationSummary] = []
        for record in records:
            turns = record.turns_json or []
            first_user = next(
                (turn for turn in turns if turn.get("role") == "user"),
                None,
            )
            title = (
                str(first_user.get("content", "New conversation"))
                if first_user
                else "New conversation"
            )
            summaries.append(
                ConversationSummary(
                    session_id=record.session_id,
                    project_id=record.project_id or DEFAULT_PROJECT_ID,
                    title=title[:80],
                    updated_at=record.updated_at,
                    turn_count=len(turns),
                )
            )
        return summaries

    async def delete(self, session_id: str, project_id: str = DEFAULT_PROJECT_ID) -> bool:
        """Delete one conversation and return whether it existed."""

        async with self._session_manager.session() as session:
            result = await session.execute(
                delete(ResearchSessionRecordModel).where(
                    ResearchSessionRecordModel.session_id == session_id,
                    ResearchSessionRecordModel.project_id == project_id,
                )
            )
        return bool(result.rowcount)
