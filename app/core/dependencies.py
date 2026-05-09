"""FastAPI dependency helpers."""

from fastapi import HTTPException, Request

from app.core.lifecycle import ServiceContainer
from app.observability.metrics import InMemoryMetricsRegistry


def get_container(request: Request) -> ServiceContainer:
    """Return the initialized service container from application state."""

    return request.app.state.container


def get_metrics_registry(request: Request) -> InMemoryMetricsRegistry:
    """Return the initialized metrics registry from application state."""

    registry = getattr(request.app.state, "metrics_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Metrics registry unavailable")
    return registry
