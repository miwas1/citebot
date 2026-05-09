"""Dense, sparse, and hybrid retrieval orchestration."""

from __future__ import annotations

import json
import math
from typing import Any

import httpx
from sqlalchemy import text

from app.core.config import Settings
from app.db.session import DatabaseSessionManager
from app.ingestion.embedder import BaseEmbedder
from app.ingestion.schemas import RetrievalFilters, SearchRequest, SearchResult
from app.ingestion.sparse_index import SparseIndex
from app.retrieval.repository import IndexedChunkRecord, RetrievalRepository
from app.retrieval.reranker import BaseReranker


class RetrievalBackendUnavailableError(RuntimeError):
    """Raised when a requested dense backend is unavailable for search."""


class LocalDenseRetriever:
    """Fallback dense retriever that scores persisted chunks in process."""

    def __init__(
        self,
        repository: RetrievalRepository,
        embedder: BaseEmbedder,
    ) -> None:
        """Store dependencies required for local dense scoring."""

        self._repository = repository
        self._embedder = embedder

    async def search(
        self,
        query_embedding: list[float],
        filters: RetrievalFilters,
        top_k: int,
    ) -> list[SearchResult]:
        """Return cosine-ranked chunks by embedding each candidate locally."""

        chunks = await self._repository.list_chunks(filters)
        if not chunks:
            return []
        chunk_embeddings = await self._embedder.embed_texts(
            [chunk.text for chunk in chunks]
        )
        results: list[SearchResult] = []
        for chunk, chunk_embedding in zip(chunks, chunk_embeddings, strict=True):
            score = _cosine_similarity(query_embedding, chunk_embedding)
            if score <= 0:
                continue
            results.append(
                _build_result(
                    chunk=chunk,
                    score=score,
                    dense_score=score,
                    source_backend="local",
                )
            )
        return sorted(results, key=lambda item: item.score, reverse=True)[:top_k]

    async def health_check(self) -> bool:
        """Return whether the local dense fallback is available."""

        return True


class PgVectorDenseRetriever:
    """Dense retriever backed by the pgvector storage table."""

    def __init__(
        self,
        session_manager: DatabaseSessionManager,
        enabled: bool,
    ) -> None:
        """Store the shared database session manager and enablement flag."""

        self._session_manager = session_manager
        self._enabled = enabled

    async def search(
        self,
        query_embedding: list[float],
        filters: RetrievalFilters,
        top_k: int,
    ) -> list[SearchResult]:
        """Return dense matches by reading persisted pgvector rows and scoring them."""

        if not self._enabled:
            msg = "pgvector search is disabled"
            raise RetrievalBackendUnavailableError(msg)
        rows = await self._load_rows()
        results: list[SearchResult] = []
        for row in rows:
            if not _matches_filters(row, filters):
                continue
            embedding = _parse_vector_literal(row["embedding"])
            score = _cosine_similarity(query_embedding, embedding)
            if score <= 0:
                continue
            results.append(
                _build_result(
                    chunk=_row_to_chunk(row),
                    score=score,
                    dense_score=score,
                    source_backend="pgvector",
                )
            )
        return sorted(results, key=lambda item: item.score, reverse=True)[:top_k]

    async def health_check(self) -> bool:
        """Return whether the pgvector-backed embedding table can be queried."""

        if not self._enabled:
            return False
        try:
            async with self._session_manager.session() as session:
                await session.execute(text("SELECT 1 FROM chunk_embeddings LIMIT 1"))
        except Exception:
            return False
        return True

    async def _load_rows(self) -> list[dict[str, Any]]:
        """Fetch joined embedding and metadata rows from PostgreSQL."""

        try:
            async with self._session_manager.session() as session:
                result = await session.execute(
                    text(
                        """
                        SELECT
                            ce.chunk_id,
                            ce.document_id,
                            ce.embedding::text AS embedding,
                            ce.embedding_version,
                            ce.index_version,
                            c.text,
                            c.location_marker,
                            c.section,
                            c.page,
                            d.title,
                            d.source_uri,
                            d.access_policy,
                            d.metadata_json
                        FROM chunk_embeddings AS ce
                        JOIN chunks AS c ON c.chunk_id = ce.chunk_id
                        JOIN documents AS d ON d.document_id = ce.document_id
                        """
                    )
                )
        except Exception as error:
            raise RetrievalBackendUnavailableError(str(error)) from error
        return [dict(row._mapping) for row in result]


class QdrantDenseRetriever:
    """Dense retriever backed by Qdrant collection search."""

    def __init__(
        self,
        base_url: str,
        collection_name: str,
        enabled: bool,
    ) -> None:
        """Store the Qdrant endpoint, collection name, and enablement flag."""

        self._base_url = base_url.rstrip("/")
        self._collection_name = collection_name
        self._enabled = enabled

    async def search(
        self,
        query_embedding: list[float],
        filters: RetrievalFilters,
        top_k: int,
    ) -> list[SearchResult]:
        """Query Qdrant and return filtered dense matches ordered by score."""

        if not self._enabled:
            msg = "qdrant search is disabled"
            raise RetrievalBackendUnavailableError(msg)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    (
                        f"{self._base_url}/collections/"
                        f"{self._collection_name}/points/search"
                    ),
                    json={
                        "vector": query_embedding,
                        "limit": max(top_k * 4, top_k),
                        "with_payload": True,
                    },
                )
                response.raise_for_status()
        except Exception as error:
            raise RetrievalBackendUnavailableError(str(error)) from error
        payload = response.json()
        points = payload.get("result", [])
        results: list[SearchResult] = []
        for point in points:
            chunk = _point_to_chunk(point)
            if not _matches_filters(chunk, filters):
                continue
            score = float(point.get("score", 0.0))
            if score <= 0:
                continue
            results.append(
                _build_result(
                    chunk=chunk,
                    score=score,
                    dense_score=score,
                    source_backend="qdrant",
                )
            )
        return sorted(results, key=lambda item: item.score, reverse=True)[:top_k]

    async def health_check(self) -> bool:
        """Return whether Qdrant responds to a collection lookup request."""

        if not self._enabled:
            return False
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{self._base_url}/collections/{self._collection_name}"
                )
                response.raise_for_status()
        except Exception:
            return False
        return True


class RetrievalService:
    """Coordinate dense, sparse, hybrid, fallback, and reranked retrieval flows."""

    def __init__(
        self,
        settings: Settings,
        session_manager: DatabaseSessionManager,
        repository: RetrievalRepository,
        embedder: BaseEmbedder,
        sparse_index: SparseIndex,
        reranker: BaseReranker | None,
    ) -> None:
        """Store retrieval dependencies and construct the available backends."""

        self._settings = settings
        self._repository = repository
        self._embedder = embedder
        self._sparse_index = sparse_index
        self._reranker = reranker
        self._dense_backends = {
            "local": LocalDenseRetriever(repository, embedder),
            "pgvector": PgVectorDenseRetriever(
                session_manager, settings.enable_pgvector
            ),
            "qdrant": QdrantDenseRetriever(
                base_url=settings.qdrant_url,
                collection_name=settings.qdrant_collection,
                enabled=settings.enable_qdrant,
            ),
        }

    async def search(self, request: SearchRequest) -> list[SearchResult]:
        """Execute the requested retrieval strategy and return ranked search results."""

        dense_limit = max(request.top_k, self._settings.hybrid_candidate_count)
        if request.strategy == "sparse":
            results = await self._sparse_index.search(
                query=request.query,
                top_k=request.top_k,
                filters=request.filters,
            )
        elif request.strategy == "dense":
            dense_results, dense_explain = await self._search_dense(
                query=request.query,
                filters=request.filters,
                top_k=dense_limit,
                index_target=request.index_target,
            )
            results = self._attach_backend_explain(dense_results, dense_explain)
        else:
            dense_results, dense_explain = await self._search_dense(
                query=request.query,
                filters=request.filters,
                top_k=dense_limit,
                index_target=request.index_target,
            )
            sparse_results = await self._sparse_index.search(
                query=request.query,
                top_k=dense_limit,
                filters=request.filters,
            )
            results = self._fuse_results(dense_results, sparse_results, dense_explain)
        reranking_enabled = (
            self._settings.enable_reranking
            if request.enable_reranking is None
            else request.enable_reranking
        )
        if reranking_enabled and self._reranker is not None and results:
            candidate_window = max(
                request.top_k, self._settings.reranker_candidate_count
            )
            reranked_candidates = await self._reranker.rerank(
                request.query,
                results[:candidate_window],
            )
            trailing_candidates = results[candidate_window:]
            results = reranked_candidates + trailing_candidates
        final_results = results[: request.top_k]
        if not request.include_explain:
            final_results = [
                result.model_copy(update={"explain": None}) for result in final_results
            ]
        return final_results

    async def batch_search(
        self,
        requests: list[SearchRequest],
    ) -> list[list[SearchResult]]:
        """Execute a batch of retrieval requests serially with shared service state."""

        return [await self.search(request) for request in requests]

    async def health_check(self) -> dict[str, bool]:
        """Return health status for each dense backend and the sparse index."""

        pgvector_health = await self._dense_backends["pgvector"].health_check()
        qdrant_health = await self._dense_backends["qdrant"].health_check()
        local_health = await self._dense_backends["local"].health_check()
        return {
            "pgvector": pgvector_health,
            "qdrant": qdrant_health,
            "local": local_health,
            "sparse": True,
        }

    async def explain(self, request: SearchRequest) -> list[SearchResult]:
        """Return retrieval results with explanation payloads forced on."""

        explain_request = request.model_copy(update={"include_explain": True})
        return await self.search(explain_request)

    async def _search_dense(
        self,
        query: str,
        filters: RetrievalFilters,
        top_k: int,
        index_target: str,
    ) -> tuple[list[SearchResult], dict[str, Any]]:
        """Run dense retrieval through the requested backend with fallback handling."""

        query_embedding = (await self._embedder.embed_texts([query]))[0]
        backend_errors: dict[str, str] = {}
        for backend_name in self._backend_order(index_target):
            backend = self._dense_backends[backend_name]
            try:
                results = await backend.search(query_embedding, filters, top_k)
            except RetrievalBackendUnavailableError as error:
                backend_errors[backend_name] = str(error)
                continue
            explain = {
                "requested_backend": index_target,
                "used_backend": backend_name,
                "fallback_errors": backend_errors,
                "strategy": "dense",
            }
            return self._attach_backend_explain(results, explain), explain
        explain = {
            "requested_backend": index_target,
            "used_backend": "none",
            "fallback_errors": backend_errors,
            "strategy": "dense",
        }
        return [], explain

    def _backend_order(self, index_target: str) -> list[str]:
        """Return dense backend preference order for the requested target."""

        if index_target != "auto":
            if index_target == "local":
                return ["local"]
            return [index_target, "local"]
        configured_primary = self._settings.dense_primary_backend
        if configured_primary == "auto":
            configured_primary = (
                "qdrant" if self._settings.enable_qdrant else "pgvector"
            )
            if configured_primary not in {"pgvector", "qdrant"}:
                configured_primary = "local"
        if configured_primary == "qdrant":
            return ["qdrant", "pgvector", "local"]
        if configured_primary == "pgvector":
            return ["pgvector", "qdrant", "local"]
        return ["local"]

    def _fuse_results(
        self,
        dense_results: list[SearchResult],
        sparse_results: list[SearchResult],
        dense_explain: dict[str, Any],
    ) -> list[SearchResult]:
        """Combine dense and sparse results with normalization, fusion, and deduping."""

        max_dense = max(
            (result.dense_score or 0.0 for result in dense_results), default=1.0
        )
        max_sparse = max(
            (result.sparse_score or 0.0 for result in sparse_results), default=1.0
        )
        dense_ranks = {
            result.chunk_id: index
            for index, result in enumerate(dense_results, start=1)
        }
        sparse_ranks = {
            result.chunk_id: index
            for index, result in enumerate(sparse_results, start=1)
        }
        merged: dict[str, SearchResult] = {}
        for result in dense_results:
            dense_score = result.dense_score or result.score
            normalized_dense = dense_score / max(max_dense, 1e-9)
            explanation = dict(result.explain or {})
            explanation.update(dense_explain)
            merged[result.chunk_id] = result.model_copy(
                update={
                    "score": normalized_dense,
                    "dense_score": dense_score,
                    "fused_score": normalized_dense,
                    "source_backend": "hybrid",
                    "explain": explanation,
                }
            )
        for result in sparse_results:
            sparse_score = result.sparse_score or result.score
            normalized_sparse = sparse_score / max(max_sparse, 1e-9)
            existing = merged.get(result.chunk_id)
            dense_component = (
                existing.dense_score / max(max_dense, 1e-9)
                if existing and existing.dense_score
                else 0.0
            )
            reciprocal_rank_bonus = _rrf_score(
                dense_rank=dense_ranks.get(result.chunk_id),
                sparse_rank=sparse_ranks.get(result.chunk_id),
            )
            fused_score = (
                dense_component * self._settings.hybrid_dense_weight
                + normalized_sparse * self._settings.hybrid_sparse_weight
                + reciprocal_rank_bonus
            )
            base_result = existing or result
            explanation = dict(base_result.explain or {})
            explanation.update(
                {
                    **dense_explain,
                    "dense_rank": dense_ranks.get(result.chunk_id),
                    "sparse_rank": sparse_ranks.get(result.chunk_id),
                    "fusion_method": "weighted_rrf",
                }
            )
            merged[result.chunk_id] = base_result.model_copy(
                update={
                    "score": fused_score,
                    "dense_score": existing.dense_score if existing else None,
                    "sparse_score": sparse_score,
                    "fused_score": fused_score,
                    "source_backend": "hybrid",
                    "explain": explanation,
                }
            )
        return sorted(merged.values(), key=lambda item: item.score, reverse=True)

    def _attach_backend_explain(
        self,
        results: list[SearchResult],
        explain: dict[str, Any],
    ) -> list[SearchResult]:
        """Attach common backend explanation fields to each result."""

        attached_results: list[SearchResult] = []
        for result in results:
            explanation = dict(result.explain or {})
            explanation.update(explain)
            attached_results.append(result.model_copy(update={"explain": explanation}))
        return attached_results


def _build_result(
    chunk: IndexedChunkRecord,
    score: float,
    source_backend: str,
    dense_score: float | None = None,
    sparse_score: float | None = None,
) -> SearchResult:
    """Build a SearchResult from a chunk payload and backend-specific scores."""

    return SearchResult(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        title=chunk.title,
        source_uri=chunk.source_uri,
        location_marker=chunk.location_marker,
        score=score,
        text=chunk.text,
        dense_score=dense_score,
        sparse_score=sparse_score,
        source_backend=source_backend,
        metadata={
            "access_policy": chunk.access_policy,
            "embedding_version": chunk.embedding_version,
            "index_version": chunk.index_version,
            "section": chunk.section,
            "page": chunk.page,
            "document_metadata": chunk.document_metadata,
        },
    )


def _row_to_chunk(row: dict[str, Any]) -> IndexedChunkRecord:
    """Convert a pgvector join row into the shared retrieval chunk payload."""

    metadata_json = row.get("metadata_json") or {}
    if isinstance(metadata_json, str):
        metadata_json = json.loads(metadata_json)
    return IndexedChunkRecord(
        chunk_id=row["chunk_id"],
        document_id=row["document_id"],
        title=row["title"],
        source_uri=row["source_uri"],
        location_marker=row.get("location_marker"),
        text=row["text"],
        access_policy=row.get("access_policy", "internal"),
        embedding_version=row["embedding_version"],
        index_version=row["index_version"],
        section=row.get("section"),
        page=row.get("page"),
        document_metadata=dict(metadata_json),
    )


def _point_to_chunk(point: dict[str, Any]) -> IndexedChunkRecord:
    """Convert a Qdrant point payload into the shared retrieval chunk payload."""

    payload = point.get("payload", {})
    metadata_json = payload.get("metadata") or {}
    return IndexedChunkRecord(
        chunk_id=str(point["id"]),
        document_id=payload["document_id"],
        title=payload.get("title", ""),
        source_uri=payload.get("source_uri", ""),
        location_marker=payload.get("location_marker"),
        text=payload.get("text", ""),
        access_policy=payload.get("access_policy", "internal"),
        embedding_version=payload.get("embedding_version", "v1"),
        index_version=payload.get("index_version", "v1"),
        section=payload.get("section"),
        page=payload.get("page"),
        document_metadata=dict(metadata_json),
    )


def _parse_vector_literal(vector_literal: str) -> list[float]:
    """Parse a pgvector text literal into a list of floats."""

    stripped = vector_literal.strip()[1:-1]
    if not stripped:
        return []
    return [float(value) for value in stripped.split(",")]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    """Compute cosine similarity between two dense vectors."""

    if not left or not right:
        return 0.0
    numerator = sum(first * second for first, second in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _rrf_score(dense_rank: int | None, sparse_rank: int | None) -> float:
    """Return a small reciprocal-rank-fusion bonus for merged candidates."""

    constant = 60
    score = 0.0
    if dense_rank is not None:
        score += 1 / (constant + dense_rank)
    if sparse_rank is not None:
        score += 1 / (constant + sparse_rank)
    return score


def _matches_filters(
    chunk: IndexedChunkRecord,
    filters: RetrievalFilters,
) -> bool:
    """Return whether an indexed chunk satisfies the requested filters."""

    if filters.document_ids and chunk.document_id not in filters.document_ids:
        return False
    if filters.source_uris and chunk.source_uri not in filters.source_uris:
        return False
    if filters.access_policies and chunk.access_policy not in filters.access_policies:
        return False
    if (
        filters.embedding_version is not None
        and chunk.embedding_version != filters.embedding_version
    ):
        return False
    if (
        filters.index_version is not None
        and chunk.index_version != filters.index_version
    ):
        return False
    return True
