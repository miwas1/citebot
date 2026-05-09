"""Liveness, readiness, and version endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.core.config import Settings, get_settings
from app.core.dependencies import get_container, get_metrics_registry
from app.core.lifecycle import ServiceContainer
from app.core.security import require_admin_access
from app.observability.metrics import InMemoryMetricsRegistry

router = APIRouter()

ContainerDependency = Annotated[ServiceContainer, Depends(get_container)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]
MetricsDependency = Annotated[InMemoryMetricsRegistry, Depends(get_metrics_registry)]
AdminAccessDependency = Annotated[None, Depends(require_admin_access)]


@router.get("/health")
async def health() -> dict[str, str]:
    """Return a basic liveness response."""

    return {"status": "ok"}


@router.get("/ready")
async def readiness(
    settings: SettingsDependency,
    container: ContainerDependency,
) -> JSONResponse:
    """Return dependency-aware readiness information for the running service."""

    payload = await container.health_service.readiness()
    status_code = 200 if payload["status"] == "ready" else 503
    return JSONResponse(status_code=status_code, content=payload)


@router.get("/version")
async def version(settings: SettingsDependency) -> dict[str, str]:
    """Return the deployed application version."""

    return {"name": settings.app_name, "version": settings.app_version}


@router.get("/metrics")
async def metrics_snapshot(
    metrics_registry: MetricsDependency,
    _: AdminAccessDependency,
) -> dict[str, object]:
    """Return an in-process metrics snapshot for lower-environment diagnostics."""

    return metrics_registry.snapshot()
