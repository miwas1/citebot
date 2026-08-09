"""Embedding backends for local inference and optional compatibility providers."""

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


class LocalHttpEmbedder(LocalEmbedder):
    """Call a local OpenAI-compatible embedding service over the private network.

    The class inherits the deterministic implementation for backwards-compatible
    type checks, but never falls back to it when the local service is unavailable.
    ``LocalEmbedder`` remains the explicit test provider.
    """

    def __init__(
        self,
        base_url: str,
        model_name: str,
        dimensions: int,
        timeout_seconds: float = 30.0,
        batch_size: int = 8,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(dimensions)
        self._base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._dimensions = dimensions
        self._timeout_seconds = timeout_seconds
        self._batch_size = batch_size
        self._transport = transport

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed text batches through the configured local service."""

        values = list(texts)
        if not values:
            return []
        if len(values) > self._batch_size:
            results: list[list[float]] = []
            for start in range(0, len(values), self._batch_size):
                results.extend(await self.embed_texts(values[start : start + self._batch_size]))
            return results

        openai_endpoint = self._base_url
        if not openai_endpoint.endswith("/v1"):
            openai_endpoint = f"{openai_endpoint}/v1"
        endpoints = [
            (f"{openai_endpoint}/embeddings", {"model": self._model_name, "input": values}),
            (f"{self._base_url}/embed", {"inputs": values}),
        ]
        response: httpx.Response | None = None
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                for endpoint, payload in endpoints:
                    candidate = await client.post(endpoint, json=payload)
                    if candidate.status_code == 404 and endpoint != endpoints[-1][0]:
                        continue
                    candidate.raise_for_status()
                    response = candidate
                    break
        except httpx.HTTPError as error:
            raise RuntimeError(f"Local embedding service unavailable: {error}") from error
        if response is None:
            raise RuntimeError("Local embedding service returned no response")
        body = response.json()
        raw_embeddings = (
            body
            if isinstance(body, list)
            else body.get("data") or body.get("embeddings")
        )
        if not isinstance(raw_embeddings, list):
            raise RuntimeError("Local embedding service returned no embeddings")
        if raw_embeddings and isinstance(raw_embeddings[0], dict):
            raw_embeddings = [item.get("embedding") for item in raw_embeddings]
        embeddings = [list(vector) for vector in raw_embeddings]
        if len(embeddings) != len(values):
            raise RuntimeError("Local embedding service returned the wrong batch size")
        if any(len(vector) != self._dimensions for vector in embeddings):
            raise RuntimeError(
                "Local embedding dimension mismatch: "
                f"expected {self._dimensions}"
            )
        return embeddings


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

    if settings.embedding_provider == "local-http":
        return LocalHttpEmbedder(
            base_url=settings.embedding_base_url,
            model_name=settings.embedding_model,
            dimensions=settings.embedding_dimension,
            timeout_seconds=settings.embedding_timeout_seconds,
            batch_size=settings.embedding_batch_size,
        )
    if settings.embedding_provider in {"local", "test"}:
        return LocalEmbedder(settings.embedding_dimension)
    if settings.embedding_provider == "openai":
        if settings.runtime_mode == "offline":
            raise ValueError("OpenAI embeddings are disabled in RUNTIME_MODE=offline")
        if not settings.openai_api_key:
            msg = "OPENAI_API_KEY is required when EMBEDDING_PROVIDER=openai"
            raise ValueError(msg)
        return OpenAIEmbedder(settings.openai_api_key, settings.embedding_model)
    if settings.embedding_provider == "gemini":
        if settings.runtime_mode == "offline":
            raise ValueError("Gemini embeddings are disabled in RUNTIME_MODE=offline")
        if not settings.gemini_api_key:
            msg = "GEMINI_API_KEY is required when EMBEDDING_PROVIDER=gemini"
            raise ValueError(msg)
        return GeminiEmbedder(
            api_key=settings.gemini_api_key,
            model_name=settings.gemini_embedding_model,
        )
    msg = "EMBEDDING_PROVIDER must be one of local-http, local, test, openai, or gemini"
    raise ValueError(msg)
