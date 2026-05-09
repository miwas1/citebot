"""Application settings and environment validation."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed runtime configuration for the API and ingestion services."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = Field(default="CiteBot", alias="APP_NAME")
    app_version: str = Field(default="0.1.0", alias="APP_VERSION")
    app_env: str = Field(default="development", alias="APP_ENV")
    api_prefix: str = Field(default="/api/v1", alias="API_PREFIX")
    database_url: str = Field(
        default="sqlite+aiosqlite:///./citebot.db", alias="DATABASE_URL"
    )
    qdrant_url: str = Field(default="http://localhost:6333", alias="QDRANT_URL")
    qdrant_collection: str = Field(default="citebot_chunks", alias="QDRANT_COLLECTION")
    enable_qdrant: bool = Field(default=False, alias="ENABLE_QDRANT")
    enable_pgvector: bool = Field(default=False, alias="ENABLE_PGVECTOR")
    object_storage_path: Path = Field(
        default=Path("./storage/raw_documents"),
        alias="OBJECT_STORAGE_PATH",
    )
    sparse_index_path: Path = Field(
        default=Path("./storage/sparse_index.json"),
        alias="SPARSE_INDEX_PATH",
    )
    embedding_provider: str = Field(default="mock", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(
        default="text-embedding-3-small", alias="EMBEDDING_MODEL"
    )
    embedding_dimension: int = Field(default=32, alias="EMBEDDING_DIMENSION")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_embedding_model: str = Field(
        default="gemini-embedding-2",
        alias="GEMINI_EMBEDDING_MODEL",
    )
    chunk_size: int = Field(default=800, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=120, alias="CHUNK_OVERLAP")

    @model_validator(mode="after")
    def validate_production_requirements(self) -> "Settings":
        """Fail fast when production-only settings are incomplete."""

        if (
            self.app_env == "production"
            and self.embedding_provider == "openai"
            and not self.openai_api_key
        ):
            msg = "OPENAI_API_KEY is required when APP_ENV=production and EMBEDDING_PROVIDER=openai"
            raise ValueError(msg)
        if (
            self.app_env == "production"
            and self.embedding_provider == "gemini"
            and not self.gemini_api_key
        ):
            msg = "GEMINI_API_KEY is required when APP_ENV=production and EMBEDDING_PROVIDER=gemini"
            raise ValueError(msg)
        if self.chunk_overlap >= self.chunk_size:
            msg = "CHUNK_OVERLAP must be smaller than CHUNK_SIZE"
            raise ValueError(msg)
        if self.enable_pgvector and not self.database_url.startswith("postgresql+"):
            msg = "ENABLE_PGVECTOR requires a PostgreSQL DATABASE_URL"
            raise ValueError(msg)
        if self.embedding_dimension <= 0:
            msg = "EMBEDDING_DIMENSION must be positive"
            raise ValueError(msg)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings object for dependency injection."""

    return Settings()
