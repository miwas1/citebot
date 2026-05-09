"""Lightweight persistent BM25-style sparse index for local validation."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from app.ingestion.schemas import CanonicalDocument, ChunkPayload, SearchResult


class SparseIndex:
    """Persist chunk text and run local BM25-style search over the corpus."""

    def __init__(self, index_path: Path) -> None:
        """Store the path used to persist the sparse index payload."""

        self._index_path = index_path

    async def initialize(self) -> None:
        """Create the sparse index file if it does not already exist."""

        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        if not self._index_path.exists():
            self._index_path.write_text(json.dumps({"chunks": {}}), encoding="utf-8")

    async def replace_document_chunks(
        self,
        document: CanonicalDocument,
        chunks: list[ChunkPayload],
    ) -> None:
        """Replace all sparse index entries for a single document."""

        payload = self._read_index()
        payload["chunks"] = {
            chunk_id: value
            for chunk_id, value in payload["chunks"].items()
            if value["document_id"] != document.document_id
        }
        for chunk in chunks:
            payload["chunks"][chunk.chunk_id] = {
                "chunk_id": chunk.chunk_id,
                "document_id": chunk.document_id,
                "title": chunk.title,
                "source_uri": chunk.source_uri,
                "location_marker": chunk.location_marker,
                "text": chunk.text,
                "tokens": self._tokenize(chunk.text),
            }
        self._write_index(payload)

    async def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """Search indexed chunks using a BM25-style relevance score."""

        payload = self._read_index()
        chunks = list(payload["chunks"].values())
        if not chunks:
            return []
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []
        average_length = sum(len(chunk["tokens"]) for chunk in chunks) / len(chunks)
        results: list[SearchResult] = []
        for chunk in chunks:
            score = self._score_chunk(
                query_tokens, chunk["tokens"], chunks, average_length
            )
            if score <= 0:
                continue
            results.append(
                SearchResult(
                    chunk_id=chunk["chunk_id"],
                    document_id=chunk["document_id"],
                    title=chunk["title"],
                    source_uri=chunk["source_uri"],
                    location_marker=chunk.get("location_marker"),
                    score=score,
                    text=chunk["text"],
                )
            )
        return sorted(results, key=lambda item: item.score, reverse=True)[:top_k]

    def _score_chunk(
        self,
        query_tokens: list[str],
        document_tokens: list[str],
        corpus: list[dict[str, object]],
        average_length: float,
    ) -> float:
        """Compute a simple BM25 score for one chunk against the full corpus."""

        k1 = 1.5
        b = 0.75
        score = 0.0
        for term in query_tokens:
            term_frequency = document_tokens.count(term)
            if term_frequency == 0:
                continue
            document_frequency = sum(1 for item in corpus if term in item["tokens"])
            numerator = len(corpus) - document_frequency + 0.5
            denominator = document_frequency + 0.5
            inverse_document_frequency = math.log(1 + (numerator / denominator))
            length_ratio = (
                len(document_tokens) / average_length if average_length else 1.0
            )
            score += inverse_document_frequency * (
                (term_frequency * (k1 + 1))
                / (term_frequency + k1 * (1 - b + (b * length_ratio)))
            )
        return score

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize text into lowercase alphanumeric search terms."""

        return re.findall(r"[a-z0-9]+", text.lower())

    def _read_index(self) -> dict[str, dict[str, object]]:
        """Read the persisted sparse index payload from disk."""

        return json.loads(self._index_path.read_text(encoding="utf-8"))

    def _write_index(self, payload: dict[str, object]) -> None:
        """Persist the sparse index payload back to disk."""

        self._index_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
