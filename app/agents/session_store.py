"""In-memory session persistence for replayable research conversations."""

from __future__ import annotations

from app.agents.schemas import ResearchSessionRecord


class ResearchSessionStore:
    """Store structured research session state for lower-environment replay."""

    def __init__(self) -> None:
        """Initialize the in-memory session map."""

        self._records: dict[str, ResearchSessionRecord] = {}

    def get(self, session_id: str) -> ResearchSessionRecord | None:
        """Return the stored session record when one exists."""

        return self._records.get(session_id)

    def save(self, record: ResearchSessionRecord) -> None:
        """Persist the latest session record by its identifier."""

        self._records[record.session_id] = record
