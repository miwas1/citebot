"""Configuration validation tests."""

import pytest

from app.core.config import Settings


def _production_settings(**overrides: str) -> Settings:
    """Build production settings without reading a developer's local .env file."""

    values = {
        "APP_ENV": "production",
        "EMBEDDING_PROVIDER": "local",
        "ANSWER_PROVIDER": "local",
        "EVALUATION_EVALUATOR_PROVIDER": "openai",
        "OPENAI_API_KEY": "openai-test-key",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_production_requires_research_api_key() -> None:
    """Production cannot start with an unprotected research API."""

    with pytest.raises(ValueError, match="RESEARCH_API_KEY"):
        _production_settings()


def test_production_requires_admin_api_key() -> None:
    """Production cannot start with an unprotected admin API."""

    with pytest.raises(ValueError, match="ADMIN_API_KEY"):
        _production_settings(RESEARCH_API_KEY="research-test-key")


def test_production_accepts_required_api_keys() -> None:
    """Production settings load when both protected API keys are present."""

    settings = _production_settings(
        RESEARCH_API_KEY="research-test-key",
        ADMIN_API_KEY="admin-test-key",
    )

    assert settings.app_env == "production"
