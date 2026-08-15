"""Dense, sparse, and hybrid retrieval orchestration."""

from __future__ import annotations

import json
import math
from typing import Any

from sqlalchemy import text

from app.core.config import Settings
from app.db.session import DatabaseSessionManager
from app.ingestion.embedder import BaseEmbedder
from app.ingestion.schemas import RetrievalFilters, SearchRequest, SearchResult
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
        max_candidates: int,
    ) -> None:
        """Store dependencies required for local dense scoring."""

        self._repository = repository
        self._embedder = embedder
        self._max_candidates = max_candidates

    async def search(
        self,
        query_embedding: list[float],
        filters: RetrievalFilters,
        top_k: int,
    ) -> list[SearchResult]:
        """Return cosine-ranked chunks by embedding each candidate locally."""

        chunks = await self._repository.list_chunks(
            filters,
            limit=self._max_candidates + 1,
        )
        if not chunks:
            return []
        if len(chunks) > self._max_candidates:
            raise RetrievalBackendUnavailableError(
                "Local dense fallback is disabled for corpora larger than "
                f"MAX_LOCAL_DENSE_CANDIDATES={self._max_candidates}"
            )
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


class DatabaseSparseRetriever:
    """Sparse full-text retrieval from the primary relational database."""

    def __init__(
        self,
        session_manager: DatabaseSessionManager,
        repository: RetrievalRepository,
        max_candidates: int,
    ) -> None:
        self._session_manager = session_manager
        self._repository = repository
        self._max_candidates = max_candidates

    async def search(
        self,
        query: str,
        top_k: int,
        filters: RetrievalFilters,
    ) -> list[SearchResult]:
        """Rank matches with PostgreSQL FTS, with a bounded test-dialect fallback."""

        if not self._session_manager.is_postgresql:
            return await self._search_test_dialect(query, top_k, filters)
        clauses, parameters = _postgres_filter_sql(filters)
        parameters.update({"query": query, "top_k": top_k})
        statement = text(
            f"""
            WITH search_query AS (
                SELECT websearch_to_tsquery('english', :query) AS value
            )
            SELECT
                c.chunk_id,
                c.document_id,
                d.project_id,
                c.embedding_version,
                c.index_version,
                c.text,
                c.location_marker,
                c.section,
                c.page,
                c.element_ids,
                c.bbox_refs,
                c.extraction_method,
                c.min_confidence,
                c.parent_chunk_id,
                c.chunk_level,
                c.heading_path,
                c.content_hash,
                c.version_id,
                c.is_current,
                c.ordinal,
                c.source_anchor_ids,
                d.title,
                d.source_uri,
                d.access_policy,
                d.metadata_json,
                ts_rank_cd(
                    to_tsvector('english', coalesce(c.text, '')),
                    search_query.value
                ) AS rank_score
            FROM chunks AS c
            JOIN documents AS d ON d.document_id = c.document_id
            CROSS JOIN search_query
            WHERE to_tsvector('english', coalesce(c.text, '')) @@ search_query.value
              AND {' AND '.join(clauses)}
            ORDER BY rank_score DESC, c.chunk_id
            LIMIT :top_k
            """
        )
        async with self._session_manager.session() as session:
            rows = (await session.execute(statement, parameters)).mappings().all()
        return [
            _build_result(
                chunk=_row_to_chunk(dict(row)),
                score=float(row["rank_score"]),
                sparse_score=float(row["rank_score"]),
                source_backend="postgres-fts",
            )
            for row in rows
        ]

    async def health_check(self) -> bool:
        """Return whether PostgreSQL full-text search is available."""

        if not self._session_manager.is_postgresql:
            return True
        try:
            async with self._session_manager.session() as session:
                await session.execute(
                    text("SELECT to_tsvector('english', 'health check')")
                )
        except Exception:
            return False
        return True

    async def _search_test_dialect(
        self,
        query: str,
        top_k: int,
        filters: RetrievalFilters,
    ) -> list[SearchResult]:
        """Provide deterministic bounded sparse matching for dependency-free tests."""

        terms = [term for term in query.lower().split() if term][:64]
        if not terms:
            return []
        chunks = await self._repository.list_chunks(
            filters,
            limit=self._max_candidates,
        )
        ranked: list[tuple[float, IndexedChunkRecord]] = []
        for chunk in chunks:
            haystack = f"{chunk.title} {chunk.text}".lower()
            score = float(sum(haystack.count(term) for term in terms))
            if score > 0:
                ranked.append((score, chunk))
        ranked.sort(key=lambda item: (-item[0], item[1].chunk_id))
        return [
            _build_result(
                chunk=chunk,
                score=score,
                sparse_score=score,
                source_backend="database-sparse-test",
            )
            for score, chunk in ranked[:top_k]
        ]


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
        if not self._session_manager.is_postgresql:
            msg = "pgvector requires PostgreSQL"
            raise RetrievalBackendUnavailableError(msg)
        clauses, parameters = _postgres_filter_sql(filters)
        parameters.update(
            {
                "query_embedding": _vector_literal(query_embedding),
                "top_k": top_k,
            }
        )
        try:
            async with self._session_manager.session() as session:
                await session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
                rows = (
                    await session.execute(
                        text(
                            f"""
                            SELECT
                                ce.chunk_id,
                                ce.document_id,
                                d.project_id,
                                ce.embedding_version,
                                ce.index_version,
                                c.text,
                                c.location_marker,
                                c.section,
                                c.page,
                                c.element_ids,
                                c.bbox_refs,
                                c.extraction_method,
                                c.min_confidence,
                                c.parent_chunk_id,
                                c.chunk_level,
                                c.heading_path,
                                c.content_hash,
                                c.version_id,
                                c.is_current,
                                c.ordinal,
                                c.source_anchor_ids,
                                d.title,
                                d.source_uri,
                                d.access_policy,
                                d.metadata_json,
                                1 - (ce.embedding <=> CAST(:query_embedding AS vector))
                                    AS rank_score
                            FROM chunk_embeddings AS ce
                            JOIN chunks AS c ON c.chunk_id = ce.chunk_id
                            JOIN documents AS d ON d.document_id = ce.document_id
                            WHERE {' AND '.join(clauses)}
                            ORDER BY ce.embedding <=> CAST(:query_embedding AS vector)
                            LIMIT :top_k
                            """
                        ),
                        parameters,
                    )
                ).mappings().all()
        except Exception as error:
            raise RetrievalBackendUnavailableError(str(error)) from error
        return [
            _build_result(
                chunk=_row_to_chunk(dict(row)),
                score=float(row["rank_score"]),
                dense_score=float(row["rank_score"]),
                source_backend="pgvector",
            )
            for row in rows
            if float(row["rank_score"]) > 0
        ]

    async def health_check(self) -> bool:
        """Return whether the pgvector-backed embedding table can be queried."""

        if not self._enabled or not self._session_manager.is_postgresql:
            return False
        try:
            async with self._session_manager.session() as session:
                await session.execute(text("SELECT 1 FROM chunk_embeddings LIMIT 1"))
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
        reranker: BaseReranker | None,
    ) -> None:
        """Store retrieval dependencies and construct the available backends."""

        self._settings = settings
        self._repository = repository
        self._embedder = embedder
        self._sparse_retriever = DatabaseSparseRetriever(
            session_manager,
            repository,
            max_candidates=settings.max_local_dense_candidates,
        )
        self._reranker = reranker
        self._dense_backends = {
            "local": LocalDenseRetriever(
                repository,
                embedder,
                max_candidates=settings.max_local_dense_candidates,
            ),
            "pgvector": PgVectorDenseRetriever(
                session_manager, session_manager.is_postgresql
            ),
        }

    async def search(self, request: SearchRequest) -> list[SearchResult]:
        """Execute the requested retrieval strategy and return ranked search results."""

        dense_limit = max(request.top_k, self._settings.hybrid_candidate_count)
        if request.strategy == "sparse":
            results = await self._sparse_retriever.search(
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
            sparse_results = await self._sparse_retriever.search(
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
        results = self._diversify_results(results)
        final_results = results[: request.top_k]
        final_results = await self._expand_parent_context(final_results, request.filters)
        if not request.include_explain:
            final_results = [
                result.model_copy(update={"explain": None}) for result in final_results
            ]
        return final_results

    def _diversify_results(self, results: list[SearchResult]) -> list[SearchResult]:
        """Cap duplicate-document results so context budget covers multiple sources."""

        counts: dict[str, int] = {}
        selected: list[SearchResult] = []
        for result in results:
            count = counts.get(result.document_id, 0)
            if count >= 2:
                continue
            counts[result.document_id] = count + 1
            selected.append(result)
        return selected

    async def _expand_parent_context(
        self,
        results: list[SearchResult],
        filters: RetrievalFilters,
    ) -> list[SearchResult]:
        """Add bounded sibling context for structure-aware chunks."""

        parent_ids = {result.parent_chunk_id for result in results if result.parent_chunk_id}
        if not parent_ids:
            return results
        siblings = await self._repository.list_chunks(
            filters.model_copy(update={"chunk_levels": []}),
            limit=max(100, len(parent_ids) * 8),
        )
        by_parent: dict[str, list[IndexedChunkRecord]] = {}
        for sibling in siblings:
            if sibling.parent_chunk_id in parent_ids:
                by_parent.setdefault(sibling.parent_chunk_id, []).append(sibling)
        expanded: list[SearchResult] = []
        for result in results:
            if not result.parent_chunk_id:
                expanded.append(result)
                continue
            parent_siblings = sorted(
                by_parent.get(result.parent_chunk_id, []),
                key=lambda item: item.ordinal,
            )
            selected = [
                sibling
                for sibling in parent_siblings
                if sibling.chunk_id != result.chunk_id
            ][:2]
            if not selected:
                expanded.append(result)
                continue
            pieces = [result.text, *(sibling.text for sibling in selected)]
            combined = "\n\n".join(dict.fromkeys(piece for piece in pieces if piece))
            metadata = dict(result.metadata)
            metadata["expanded_sibling_chunk_ids"] = [item.chunk_id for item in selected]
            expanded.append(
                result.model_copy(
                    update={"text": combined[:1600], "metadata": metadata}
                )
            )
        return expanded

    async def batch_search(
        self,
        requests: list[SearchRequest],
    ) -> list[list[SearchResult]]:
        """Execute a batch of retrieval requests serially with shared service state."""

        return [await self.search(request) for request in requests]

    async def health_check(self) -> dict[str, bool]:
        """Return health status for each dense backend and the sparse index."""

        pgvector_health = await self._dense_backends["pgvector"].health_check()
        local_health = await self._dense_backends["local"].health_check()
        sparse_health = await self._sparse_retriever.health_check()
        return {
            "pgvector": pgvector_health,
            "local": local_health,
            "sparse": sparse_health,
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
            backends = [index_target]
            if self._settings.allow_local_dense_fallback:
                backends.append("local")
            return backends
        configured_primary = self._settings.dense_primary_backend
        if configured_primary == "auto":
            configured_primary = "pgvector"
        if configured_primary == "pgvector":
            backends = ["pgvector"]
            if self._settings.allow_local_dense_fallback:
                backends.append("local")
            return backends
        return ["local"] if self._settings.allow_local_dense_fallback else []

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
        project_id=chunk.project_id,
        title=chunk.title,
        source_uri=chunk.source_uri,
        location_marker=chunk.location_marker,
        page=chunk.page,
        element_ids=chunk.element_ids,
        bbox_refs=chunk.bbox_refs,
        extraction_method=chunk.extraction_method,
        min_confidence=chunk.min_confidence,
        parent_chunk_id=chunk.parent_chunk_id,
        chunk_level=chunk.chunk_level,
        heading_path=chunk.heading_path,
        version_id=chunk.version_id,
        is_current=chunk.is_current,
        source_anchor_ids=chunk.source_anchor_ids,
        ordinal=chunk.ordinal,
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
            "element_ids": chunk.element_ids,
            "bbox_refs": chunk.bbox_refs,
            "extraction_method": chunk.extraction_method,
            "min_confidence": chunk.min_confidence,
            "parent_chunk_id": chunk.parent_chunk_id,
            "chunk_level": chunk.chunk_level,
            "heading_path": chunk.heading_path,
            "version_id": chunk.version_id,
            "is_current": chunk.is_current,
            "source_anchor_ids": chunk.source_anchor_ids,
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
        project_id=row.get("project_id", "sample-project"),
        title=row["title"],
        source_uri=row["source_uri"],
        location_marker=row.get("location_marker"),
        text=row["text"],
        access_policy=row.get("access_policy", "internal"),
        embedding_version=row["embedding_version"],
        index_version=row["index_version"],
        section=row.get("section"),
        page=row.get("page"),
        element_ids=list(row.get("element_ids") or []),
        bbox_refs=list(row.get("bbox_refs") or []),
        extraction_method=row.get("extraction_method"),
        min_confidence=row.get("min_confidence"),
        parent_chunk_id=row.get("parent_chunk_id"),
        chunk_level=row.get("chunk_level", "window"),
        heading_path=list(row.get("heading_path") or []),
        version_id=row.get("version_id"),
        is_current=bool(row.get("is_current", True)),
        source_anchor_ids=list(row.get("source_anchor_ids") or []),
        ordinal=int(row.get("ordinal", 0)),
        document_metadata=dict(metadata_json),
    )


def _vector_literal(values: list[float]) -> str:
    """Serialize a dense vector for pgvector's text input format."""

    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


def _postgres_filter_sql(
    filters: RetrievalFilters,
) -> tuple[list[str], dict[str, Any]]:
    """Build project-scoped PostgreSQL predicates for both retrieval modes."""

    clauses = ["d.project_id = :project_id"]
    parameters: dict[str, Any] = {"project_id": filters.project_id}
    for field, values, expression in (
        ("document_ids", filters.document_ids, "c.document_id"),
        ("source_uris", filters.source_uris, "d.source_uri"),
        ("access_policies", filters.access_policies, "d.access_policy"),
        ("version_ids", filters.version_ids, "c.version_id"),
        ("chunk_levels", filters.chunk_levels, "c.chunk_level"),
    ):
        if values:
            clauses.append(f"{expression} = ANY(CAST(:{field} AS TEXT[]))")
            parameters[field] = values
    if filters.embedding_version is not None:
        clauses.append("c.embedding_version = :embedding_version")
        parameters["embedding_version"] = filters.embedding_version
    if filters.index_version is not None:
        clauses.append("c.index_version = :index_version")
        parameters["index_version"] = filters.index_version
    if filters.current_only:
        clauses.append("c.is_current IS TRUE")
    return clauses, parameters


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
