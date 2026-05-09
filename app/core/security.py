"""Request authentication helpers for research and admin routes."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.core.config import get_settings


def _extract_api_key(
    authorization: str | None,
    x_api_key: str | None,
) -> str | None:
    """Return the caller-provided API key from standard auth headers."""

    if x_api_key:
        return x_api_key.strip()
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def _validate_api_key(
    expected_key: str | None,
    provided_key: str | None,
    scope_name: str,
) -> None:
    """Enforce API key authentication when the scope is configured with a key."""

    if not expected_key:
        return
    if provided_key and secrets.compare_digest(provided_key, expected_key):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Missing or invalid API key for {scope_name} access.",
    )


def get_runtime_settings() -> Settings:
    """Return typed runtime settings for security dependencies."""

    return get_settings()


async def require_research_access(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    """Require a configured API key for research routes when enabled."""

    settings = get_runtime_settings()
    _validate_api_key(
        settings.research_api_key,
        _extract_api_key(authorization, x_api_key),
        "research",
    )


async def require_admin_access(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    """Require a configured API key for admin routes when enabled."""

    settings = get_runtime_settings()
    _validate_api_key(
        settings.admin_api_key,
        _extract_api_key(authorization, x_api_key),
        "admin",
    )
