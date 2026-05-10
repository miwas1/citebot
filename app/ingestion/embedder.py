"""Embedding backends for local development, OpenAI, and Gemini indexing."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

import httpx

from app.core.config import Settings


class BaseEmbedder:
    """Common interface for embedding text batches."""

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Return embedding vectors for the provided text payloads."""

        raise NotImplementedError


class LocalEmbedder(BaseEmbedder):
    """Generate deterministic local embeddings without external network calls."""

    def __init__(self, dimensions: int) -> None:
        """Set the target vector dimensionality for hashed embeddings."""

        self._dimensions = dimensions

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed each text into a deterministic pseudo-vector for local runs."""

        return [self._embed_single_text(text) for text in texts]

    def _embed_single_text(self, text: str) -> list[float]:
        """Project text into a repeatable token-aware vector for local similarity tests."""

        values = [0.0] * self._dimensions
        for token in self._tokenize(text):
            token_seed = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(token_seed[:4], byteorder="big") % self._dimensions
            direction = 1.0 if token_seed[4] % 2 == 0 else -1.0
            magnitude = 0.5 + (token_seed[5] / 255.0)
            values[index] += direction * magnitude
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0:
            return values
        return [value / norm for value in values]

    def _tokenize(self, text: str) -> list[str]:
        """Split text into lowercase search-style tokens for deterministic embeddings."""

        return re.findall(r"[a-z0-9]+", text.lower())


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
                # The single embedContent API returns {"embedding": {"values": [...]}}
                if "embedding" in payload:
                    embeddings.append(payload["embedding"]["values"])
                elif "embeddings" in payload:
                    # Fallback for batch-style response if the API behaves differently
                    embeddings.append(payload["embeddings"][0]["values"])
                else:
                    msg = f"Unexpected Gemini API response: {payload}"
                    raise KeyError(msg)
        return embeddings


def build_embedder(settings: Settings) -> BaseEmbedder:
    """Build the configured embedding backend for the current environment."""

    if settings.embedding_provider == "local":
        return LocalEmbedder(settings.embedding_dimension)
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
    msg = "EMBEDDING_PROVIDER must be one of local, openai, or gemini"
    raise ValueError(msg)
