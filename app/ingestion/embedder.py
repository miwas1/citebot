"""Embedding backends for local development, OpenAI, and Gemini indexing."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import httpx

from app.core.config import Settings


class BaseEmbedder:
    """Common interface for embedding text batches."""

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Return embedding vectors for the provided text payloads."""

        raise NotImplementedError


class MockEmbedder(BaseEmbedder):
    """Generate deterministic local embeddings without external network calls."""

    def __init__(self, dimensions: int) -> None:
        """Set the target vector dimensionality for hashed embeddings."""

        self._dimensions = dimensions

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each text into a deterministic pseudo-vector for tests and local runs."""

        return [self._embed_single_text(text) for text in texts]

    def _embed_single_text(self, text: str) -> list[float]:
        """Project a text string into a repeatable float vector."""

        seed = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        while len(values) < self._dimensions:
            seed = hashlib.sha256(seed).digest()
            for byte in seed:
                values.append((byte / 255.0) * 2 - 1)
                if len(values) == self._dimensions:
                    break
        return values


class OpenAIEmbedder(BaseEmbedder):
    """Call the OpenAI embeddings API with bounded retries."""

    def __init__(self, api_key: str, model_name: str) -> None:
        """Store the OpenAI credentials and embedding model name."""

        self._api_key = api_key
        self._model_name = model_name

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Request embeddings from OpenAI for the provided input texts."""

        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers=headers,
                json={"model": self._model_name, "input": list(texts)},
            )
            response.raise_for_status()
        payload = response.json()
        return [item["embedding"] for item in payload["data"]]


class GeminiEmbedder(BaseEmbedder):
    """Call the Gemini embeddings API for text batches."""

    def __init__(self, api_key: str, model_name: str) -> None:
        """Store the Gemini credentials and embedding model name."""

        self._api_key = api_key
        self._model_name = model_name

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Request embeddings from Gemini for the provided input texts."""

        async with httpx.AsyncClient(timeout=30.0) as client:
            embeddings: list[list[float]] = []
            for text in texts:
                response = await client.post(
                    (
                        "https://generativelanguage.googleapis.com/v1beta/"
                        f"models/{self._model_name}:embedContent"
                    ),
                    headers={
                        "x-goog-api-key": self._api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": f"models/{self._model_name}",
                        "content": {"parts": [{"text": text}]},
                    },
                )
                response.raise_for_status()
                payload = response.json()
                embeddings.append(payload["embeddings"][0]["value"])
        return embeddings


def build_embedder(settings: Settings) -> BaseEmbedder:
    """Build the configured embedding backend for the current environment."""

    if settings.embedding_provider == "openai":
        if not settings.openai_api_key:
            msg = "OPENAI_API_KEY is required when EMBEDDING_PROVIDER=openai"
            raise ValueError(msg)
        return OpenAIEmbedder(settings.openai_api_key, settings.embedding_model)
    if settings.embedding_provider == "gemini":
        if not settings.gemini_api_key:
            msg = "GEMINI_API_KEY is required when EMBEDDING_PROVIDER=gemini"
            raise ValueError(msg)
        return GeminiEmbedder(
            api_key=settings.gemini_api_key,
            model_name=settings.gemini_embedding_model,
        )
    return MockEmbedder(settings.embedding_dimension)
