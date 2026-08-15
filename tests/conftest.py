"""Shared pytest fixtures for CiteBot tests."""

from pathlib import Path

import pytest

from app.core.config import get_settings


@pytest.fixture
def configured_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Configure an isolated database and storage environment for each test."""

    database_path = tmp_path / "test.db"
    object_storage = tmp_path / "storage" / "raw_documents"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    monkeypatch.setenv("OBJECT_STORAGE_PATH", str(object_storage))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("ANSWER_PROVIDER", "local")
    get_settings.cache_clear()
    return tmp_path


@pytest.fixture(autouse=True)
def clear_settings_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Reset cached settings and isolate tests from any developer .env file."""

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("OBJECT_STORAGE_PATH", str(tmp_path / "storage" / "raw_documents"))
    monkeypatch.setenv("EMBEDDING_PROVIDER", "local")
    monkeypatch.setenv("ANSWER_PROVIDER", "local")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("RESEARCH_API_KEY", "")
    monkeypatch.setenv("ADMIN_API_KEY", "")
    monkeypatch.setenv("API_KEY_AUTH_ENABLED", "false")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    monkeypatch.setenv("ALLOW_WEB_SEARCH_DEFAULT", "false")
    monkeypatch.setenv("EVALUATION_EVALUATOR_PROVIDER", "openai")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
