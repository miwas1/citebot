"""FastAPI dependency helpers."""

from fastapi import Request

from app.core.lifecycle import ServiceContainer


def get_container(request: Request) -> ServiceContainer:
    """Return the initialized service container from application state."""

    return request.app.state.container
