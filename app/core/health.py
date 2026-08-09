"""Health and readiness checks for local runtime dependencies."""

import httpx

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
        embedding_ok = await self._ping_local_service(
            self._settings.embedding_base_url,
            self._settings.embedding_provider == "local-http",
        )
        llm_ok = await self._ping_local_service(
            self._settings.llm_base_url,
            self._settings.answer_provider == "llama-cpp",
        )
        status = (
            "ready"
            if database_ok and qdrant_ok and embedding_ok and llm_ok
            else "degraded"
        )
        return {
            "status": status,
            "environment": self._settings.app_env,
            "runtime_mode": self._settings.runtime_mode,
            "dependencies": {
                "database": database_ok,
                "qdrant": qdrant_ok,
                "embedding": embedding_ok,
                "llm": llm_ok,
            },
        }

    async def _ping_local_service(self, base_url: str, enabled: bool) -> bool:
        """Ping an enabled local model service without following redirects."""

        if not enabled:
            return True
        root = base_url.rstrip("/")
        endpoint = f"{root}/health"
        if root.endswith("/v1"):
            endpoint = f"{root[:-3].rstrip('/')}/health"
        try:
            async with httpx.AsyncClient(timeout=2.0, follow_redirects=False) as client:
                response = await client.get(endpoint)
            return response.is_success
        except httpx.HTTPError:
            return False
