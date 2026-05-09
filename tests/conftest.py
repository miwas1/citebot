"""Shared pytest fixtures for CiteBot tests."""

from pathlib import Path

import pytest

from app.core.config import get_settings


@pytest.fixture
def configured_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Configure an isolated database and storage environment for each test."""

    database_path = tmp_path / "test.db"
    object_storage = tmp_path / "storage" / "raw_documents"
    sparse_index = tmp_path / "storage" / "sparse_index.json"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    monkeypatch.setenv("OBJECT_STORAGE_PATH", str(object_storage))
    monkeypatch.setenv("SPARSE_INDEX_PATH", str(sparse_index))
    monkeypatch.setenv("ENABLE_QDRANT", "false")
    monkeypatch.setenv("ENABLE_PGVECTOR", "false")
    get_settings.cache_clear()
    return tmp_path


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    """Reset cached settings before and after each test."""

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
