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
    embedding_provider: str = Field(default="local", alias="EMBEDDING_PROVIDER")
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
    dense_primary_backend: str = Field(
        default="auto",
        alias="DENSE_PRIMARY_BACKEND",
    )
    hybrid_dense_weight: float = Field(default=0.6, alias="HYBRID_DENSE_WEIGHT")
    hybrid_sparse_weight: float = Field(default=0.4, alias="HYBRID_SPARSE_WEIGHT")
    hybrid_candidate_count: int = Field(
        default=12,
        alias="HYBRID_CANDIDATE_COUNT",
    )
    dense_search_limit: int = Field(default=256, alias="DENSE_SEARCH_LIMIT")
    enable_reranking: bool = Field(default=True, alias="ENABLE_RERANKING")
    reranker_provider: str = Field(default="heuristic", alias="RERANKER_PROVIDER")
    reranker_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        alias="RERANKER_MODEL",
    )
    reranker_candidate_count: int = Field(
        default=8,
        alias="RERANKER_CANDIDATE_COUNT",
    )
    answer_provider: str = Field(default="local", alias="ANSWER_PROVIDER")
    answer_model: str = Field(default="gpt-5", alias="ANSWER_MODEL")
    gemini_answer_model: str = Field(
        default="gemini-3-flash-preview",
        alias="GEMINI_ANSWER_MODEL",
    )
    research_top_k: int = Field(default=5, alias="RESEARCH_TOP_K")
    research_min_context_score: float = Field(
        default=0.2,
        alias="RESEARCH_MIN_CONTEXT_SCORE",
    )
    research_recent_turns: int = Field(default=4, alias="RESEARCH_RECENT_TURNS")
    research_summary_char_limit: int = Field(
        default=600,
        alias="RESEARCH_SUMMARY_CHAR_LIMIT",
    )
    research_context_char_limit: int = Field(
        default=320,
        alias="RESEARCH_CONTEXT_CHAR_LIMIT",
    )
    research_preserve_citation_turns: int = Field(
        default=8,
        alias="RESEARCH_PRESERVE_CITATION_TURNS",
    )
    allow_web_search_default: bool = Field(
        default=False,
        alias="ALLOW_WEB_SEARCH_DEFAULT",
    )
    allow_python_execution_default: bool = Field(
        default=False,
        alias="ALLOW_PYTHON_EXECUTION_DEFAULT",
    )
    research_api_key: str | None = Field(default=None, alias="RESEARCH_API_KEY")
    admin_api_key: str | None = Field(default=None, alias="ADMIN_API_KEY")
    rate_limit_window_seconds: int = Field(
        default=60,
        alias="RATE_LIMIT_WINDOW_SECONDS",
    )
    research_rate_limit_requests: int = Field(
        default=0,
        alias="RESEARCH_RATE_LIMIT_REQUESTS",
    )
    admin_rate_limit_requests: int = Field(
        default=0,
        alias="ADMIN_RATE_LIMIT_REQUESTS",
    )
    observability_log_level: str = Field(
        default="INFO",
        alias="OBSERVABILITY_LOG_LEVEL",
    )
    tavily_api_key: str | None = Field(default=None, alias="TAVILY_API_KEY")
    tavily_base_url: str = Field(
        default="https://api.tavily.com/search",
        alias="TAVILY_BASE_URL",
    )
    tavily_timeout_seconds: float = Field(
        default=8.0,
        alias="TAVILY_TIMEOUT_SECONDS",
    )
    tavily_max_results: int = Field(default=5, alias="TAVILY_MAX_RESULTS")
    tavily_search_depth: str = Field(
        default="basic",
        alias="TAVILY_SEARCH_DEPTH",
    )
    tavily_max_retries: int = Field(default=2, alias="TAVILY_MAX_RETRIES")
    python_sandbox_timeout_seconds: float = Field(
        default=2.0,
        alias="PYTHON_SANDBOX_TIMEOUT_SECONDS",
    )
    python_sandbox_memory_mb: int = Field(
        default=128,
        alias="PYTHON_SANDBOX_MEMORY_MB",
    )
    python_sandbox_output_bytes: int = Field(
        default=4000,
        alias="PYTHON_SANDBOX_OUTPUT_BYTES",
    )
    evaluation_dataset_path: Path = Field(
        default=Path("./data/evaluations/tiny_smoke.json"),
        alias="EVALUATION_DATASET_PATH",
    )
    evaluation_artifact_dir: Path = Field(
        default=Path("./artifacts/evaluations"),
        alias="EVALUATION_ARTIFACT_DIR",
    )
    evaluation_enable_ragas: bool = Field(
        default=False,
        alias="EVALUATION_ENABLE_RAGAS",
    )
    evaluation_ci_fail_on_missing_ragas: bool = Field(
        default=False,
        alias="EVALUATION_CI_FAIL_ON_MISSING_RAGAS",
    )
    evaluation_faithfulness_threshold: float = Field(
        default=0.7,
        alias="EVALUATION_FAITHFULNESS_THRESHOLD",
    )
    evaluation_context_precision_threshold: float = Field(
        default=0.5,
        alias="EVALUATION_CONTEXT_PRECISION_THRESHOLD",
    )
    evaluation_answer_relevance_threshold: float = Field(
        default=0.5,
        alias="EVALUATION_ANSWER_RELEVANCE_THRESHOLD",
    )
    evaluation_citation_support_threshold: float = Field(
        default=1.0,
        alias="EVALUATION_CITATION_SUPPORT_THRESHOLD",
    )
    evaluation_evaluator_provider: str = Field(
        default="openai",
        alias="EVALUATION_EVALUATOR_PROVIDER",
    )
    evaluation_evaluator_model: str = Field(
        default="gpt-5",
        alias="EVALUATION_EVALUATOR_MODEL",
    )
    evaluation_phoenix_endpoint: str | None = Field(
        default=None,
        alias="EVALUATION_PHOENIX_ENDPOINT",
    )
    evaluation_phoenix_sample_rate: float = Field(
        default=1.0,
        alias="EVALUATION_PHOENIX_SAMPLE_RATE",
    )

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
        if self.embedding_provider not in {"local", "openai", "gemini"}:
            msg = "EMBEDDING_PROVIDER must be one of local, openai, or gemini"
            raise ValueError(msg)
        if self.dense_primary_backend not in {"auto", "pgvector", "qdrant", "local"}:
            msg = (
                "DENSE_PRIMARY_BACKEND must be one of auto, pgvector, qdrant, or local"
            )
            raise ValueError(msg)
        if self.hybrid_dense_weight < 0 or self.hybrid_sparse_weight < 0:
            msg = "HYBRID_DENSE_WEIGHT and HYBRID_SPARSE_WEIGHT must be non-negative"
            raise ValueError(msg)
        if self.hybrid_dense_weight + self.hybrid_sparse_weight == 0:
            msg = "HYBRID_DENSE_WEIGHT and HYBRID_SPARSE_WEIGHT cannot both be zero"
            raise ValueError(msg)
        if self.hybrid_candidate_count <= 0:
            msg = "HYBRID_CANDIDATE_COUNT must be positive"
            raise ValueError(msg)
        if self.dense_search_limit <= 0:
            msg = "DENSE_SEARCH_LIMIT must be positive"
            raise ValueError(msg)
        if self.reranker_candidate_count <= 0:
            msg = "RERANKER_CANDIDATE_COUNT must be positive"
            raise ValueError(msg)
        if self.answer_provider not in {"local", "openai", "gemini"}:
            msg = "ANSWER_PROVIDER must be one of local, openai, or gemini"
            raise ValueError(msg)
        if self.research_top_k <= 0:
            msg = "RESEARCH_TOP_K must be positive"
            raise ValueError(msg)
        if self.research_recent_turns <= 0:
            msg = "RESEARCH_RECENT_TURNS must be positive"
            raise ValueError(msg)
        if self.research_summary_char_limit <= 0:
            msg = "RESEARCH_SUMMARY_CHAR_LIMIT must be positive"
            raise ValueError(msg)
        if self.research_context_char_limit <= 0:
            msg = "RESEARCH_CONTEXT_CHAR_LIMIT must be positive"
            raise ValueError(msg)
        if self.research_preserve_citation_turns <= 0:
            msg = "RESEARCH_PRESERVE_CITATION_TURNS must be positive"
            raise ValueError(msg)
        if not 0 <= self.research_min_context_score <= 1:
            msg = "RESEARCH_MIN_CONTEXT_SCORE must be between 0 and 1"
            raise ValueError(msg)
        if self.rate_limit_window_seconds <= 0:
            msg = "RATE_LIMIT_WINDOW_SECONDS must be positive"
            raise ValueError(msg)
        if self.research_rate_limit_requests < 0:
            msg = "RESEARCH_RATE_LIMIT_REQUESTS cannot be negative"
            raise ValueError(msg)
        if self.admin_rate_limit_requests < 0:
            msg = "ADMIN_RATE_LIMIT_REQUESTS cannot be negative"
            raise ValueError(msg)
        if self.observability_log_level not in {
            "CRITICAL",
            "ERROR",
            "WARNING",
            "INFO",
            "DEBUG",
        }:
            msg = (
                "OBSERVABILITY_LOG_LEVEL must be one of CRITICAL, ERROR, WARNING, "
                "INFO, or DEBUG"
            )
            raise ValueError(msg)
        if self.tavily_search_depth not in {"basic", "advanced", "fast", "ultra-fast"}:
            msg = "TAVILY_SEARCH_DEPTH must be one of basic, advanced, fast, or ultra-fast"
            raise ValueError(msg)
        if self.tavily_max_results <= 0 or self.tavily_max_results > 20:
            msg = "TAVILY_MAX_RESULTS must be between 1 and 20"
            raise ValueError(msg)
        if self.tavily_max_retries < 0:
            msg = "TAVILY_MAX_RETRIES cannot be negative"
            raise ValueError(msg)
        if self.python_sandbox_timeout_seconds <= 0:
            msg = "PYTHON_SANDBOX_TIMEOUT_SECONDS must be positive"
            raise ValueError(msg)
        if self.python_sandbox_memory_mb <= 0:
            msg = "PYTHON_SANDBOX_MEMORY_MB must be positive"
            raise ValueError(msg)
        if self.python_sandbox_output_bytes <= 0:
            msg = "PYTHON_SANDBOX_OUTPUT_BYTES must be positive"
            raise ValueError(msg)
        if not 0 <= self.evaluation_faithfulness_threshold <= 1:
            msg = "EVALUATION_FAITHFULNESS_THRESHOLD must be between 0 and 1"
            raise ValueError(msg)
        if not 0 <= self.evaluation_context_precision_threshold <= 1:
            msg = "EVALUATION_CONTEXT_PRECISION_THRESHOLD must be between 0 and 1"
            raise ValueError(msg)
        if not 0 <= self.evaluation_answer_relevance_threshold <= 1:
            msg = "EVALUATION_ANSWER_RELEVANCE_THRESHOLD must be between 0 and 1"
            raise ValueError(msg)
        if not 0 <= self.evaluation_citation_support_threshold <= 1:
            msg = "EVALUATION_CITATION_SUPPORT_THRESHOLD must be between 0 and 1"
            raise ValueError(msg)
        if self.evaluation_evaluator_provider not in {"openai", "gemini"}:
            msg = "EVALUATION_EVALUATOR_PROVIDER must be one of openai or gemini"
            raise ValueError(msg)
        if self.allow_web_search_default and not self.tavily_api_key:
            msg = "TAVILY_API_KEY is required when ALLOW_WEB_SEARCH_DEFAULT=true"
            raise ValueError(msg)
        if not 0 < self.evaluation_phoenix_sample_rate <= 1:
            msg = "EVALUATION_PHOENIX_SAMPLE_RATE must be between 0 and 1"
            raise ValueError(msg)
        if (
            self.app_env == "production"
            and self.answer_provider == "openai"
            and not self.openai_api_key
        ):
            msg = "OPENAI_API_KEY is required when APP_ENV=production and ANSWER_PROVIDER=openai"
            raise ValueError(msg)
        if (
            self.app_env == "production"
            and self.answer_provider == "gemini"
            and not self.gemini_api_key
        ):
            msg = "GEMINI_API_KEY is required when APP_ENV=production and ANSWER_PROVIDER=gemini"
            raise ValueError(msg)
        if (
            self.app_env == "production"
            and self.evaluation_evaluator_provider == "openai"
            and not self.openai_api_key
        ):
            msg = "OPENAI_API_KEY is required when APP_ENV=production and EVALUATION_EVALUATOR_PROVIDER=openai"
            raise ValueError(msg)
        if (
            self.app_env == "production"
            and self.evaluation_evaluator_provider == "gemini"
            and not self.gemini_api_key
        ):
            msg = "GEMINI_API_KEY is required when APP_ENV=production and EVALUATION_EVALUATOR_PROVIDER=gemini"
            raise ValueError(msg)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings object for dependency injection."""

    return Settings()
