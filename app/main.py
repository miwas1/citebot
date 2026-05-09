"""FastAPI application bootstrap."""

from fastapi import FastAPI

from app.api.routes import build_api_router
from app.core.config import get_settings
from app.core.lifecycle import lifespan


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    settings = get_settings()
    application = FastAPI(
        title=settings.app_name, version=settings.app_version, lifespan=lifespan
    )
    application.include_router(build_api_router(), prefix=settings.api_prefix)
    return application


app = create_app()
