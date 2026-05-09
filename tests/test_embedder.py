"""Unit tests for embedding provider selection."""

import pytest

from app.core.config import Settings
from app.ingestion.embedder import (GeminiEmbedder, MockEmbedder,
                                    OpenAIEmbedder, build_embedder)


def test_build_embedder_returns_mock_provider_by_default() -> None:
    """The embedder factory should default to the deterministic mock embedder."""

    settings = Settings()

    embedder = build_embedder(settings)

    assert isinstance(embedder, MockEmbedder)


def test_build_embedder_returns_openai_provider() -> None:
    """The embedder factory should create an OpenAI embedder when requested."""

    settings = Settings(
        EMBEDDING_PROVIDER="openai",
        OPENAI_API_KEY="test-openai-key",
    )

    embedder = build_embedder(settings)

    assert isinstance(embedder, OpenAIEmbedder)


def test_build_embedder_returns_gemini_provider() -> None:
    """The embedder factory should create a Gemini embedder when requested."""

    settings = Settings(
        EMBEDDING_PROVIDER="gemini",
        GEMINI_API_KEY="test-gemini-key",
    )

    embedder = build_embedder(settings)

    assert isinstance(embedder, GeminiEmbedder)


def test_build_embedder_requires_gemini_api_key() -> None:
    """The Gemini embedder selection should fail when the API key is missing."""

    settings = Settings(EMBEDDING_PROVIDER="gemini")

    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        build_embedder(settings)        build_embedder(settings)