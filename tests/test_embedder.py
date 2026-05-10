"""Unit tests for embedding provider selection."""

import math

import pytest

from app.core.config import Settings
from app.ingestion.embedder import (
    GeminiEmbedder,
    LocalEmbedder,
    OpenAIEmbedder,
    build_embedder,
)


def test_build_embedder_returns_local_provider_by_default() -> None:
    """The embedder factory should default to the deterministic local embedder."""

    settings = Settings()

    embedder = build_embedder(settings)

    assert isinstance(embedder, LocalEmbedder)


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
        build_embedder(settings)


@pytest.mark.asyncio
async def test_local_embedder_rewards_token_overlap() -> None:
    """The local embedder should keep overlapping token content closer than unrelated text."""

    embedder = LocalEmbedder(dimensions=32)

    query_embedding, relevant_embedding, unrelated_embedding = (
        await embedder.embed_texts(
            [
                "hybrid retrieval reranking",
                "reranking improves hybrid retrieval quality",
                "kubernetes autoscaling and service mesh",
            ]
        )
    )

    relevant_similarity = sum(
        left * right
        for left, right in zip(query_embedding, relevant_embedding, strict=True)
    )
    unrelated_similarity = sum(
        left * right
        for left, right in zip(query_embedding, unrelated_embedding, strict=True)
    )

    assert math.isclose(
        math.sqrt(sum(value * value for value in query_embedding)),
        1.0,
        rel_tol=1e-6,
    )
    assert relevant_similarity > unrelated_similarity
