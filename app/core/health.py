"""Health and readiness checks for external dependencies."""

from app.core.config import Settings
from app.db.session import DatabaseSessionManager
from app.ingestion.vector_writers import QdrantWriter


class HealthService:
    """Compute dependency-aware readiness information for the API."""

    def __init__(
        self,
        settings: Settings,
        session_manager: DatabaseSessionManager,
        qdrant_writer: QdrantWriter,
    ) -> None:
        """Store the dependencies needed for liveness and readiness probes."""

        self._settings = settings
        self._session_manager = session_manager
        self._qdrant_writer = qdrant_writer

    async def readiness(self) -> dict[str, object]:
        """Return a dependency summary and an overall readiness state."""

        database_ok = await self._session_manager.ping()
        qdrant_ok = True
        if self._settings.enable_qdrant:
            qdrant_ok = await self._qdrant_writer.ping()
        status = "ready" if database_ok and qdrant_ok else "degraded"
        return {
            "status": status,
            "environment": self._settings.app_env,
            "dependencies": {"database": database_ok, "qdrant": qdrant_ok},
        }
