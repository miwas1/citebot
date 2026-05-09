"""Route registration helpers."""

from fastapi import APIRouter

from app.api.routes.admin_ingestion import router as admin_ingestion_router
from app.api.routes.health import router as health_router


def build_api_router() -> APIRouter:
    """Assemble the root API router."""

    router = APIRouter()
    router.include_router(health_router, tags=["health"])
    router.include_router(admin_ingestion_router, tags=["ingestion"])
    return router
