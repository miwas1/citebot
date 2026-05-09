"""Reranking adapters for hybrid retrieval candidate ordering."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence

from app.core.config import Settings
from app.ingestion.schemas import SearchResult


class BaseReranker:
    """Interface for rescoring retrieved candidates against the query."""

    async def rerank(
        self,
        query: str,
        results: Sequence[SearchResult],
    ) -> list[SearchResult]:
        """Return reranked search results with reranker scores attached."""

        raise NotImplementedError


class HeuristicReranker(BaseReranker):
    """Lightweight lexical reranker used by default in local development."""

    async def rerank(
        self,
        query: str,
        results: Sequence[SearchResult],
    ) -> list[SearchResult]:
        """Rescore candidates using query coverage and phrase overlap heuristics."""

        query_tokens = self._tokenize(query)
        reranked_results: list[SearchResult] = []
        for result in results:
            rerank_score = self._score(query, query_tokens, result)
            base_score = (
                result.fused_score
                or result.dense_score
                or result.sparse_score
                or result.score
            )
            blended_score = (base_score * 0.7) + (rerank_score * 0.3)
            explanation = dict(result.explain or {})
            explanation["reranker_provider"] = "heuristic"
            explanation["base_score_before_rerank"] = base_score
            reranked_results.append(
                result.model_copy(
                    update={
                        "score": blended_score,
                        "rerank_score": rerank_score,
                        "explain": explanation,
                    }
                )
            )
        return sorted(reranked_results, key=lambda item: item.score, reverse=True)

    def _score(
        self,
        query: str,
        query_tokens: list[str],
        result: SearchResult,
    ) -> float:
        """Compute a bounded reranker score for a candidate search result."""

        haystack = f"{result.title} {result.text}".lower()
        candidate_tokens = self._tokenize(haystack)
        shared_tokens = set(query_tokens) & set(candidate_tokens)
        coverage = len(shared_tokens) / max(len(set(query_tokens)), 1)
        phrase_bonus = 0.25 if query.lower() in haystack else 0.0
        title_overlap = len(
            set(query_tokens) & set(self._tokenize(result.title))
        ) / max(
            len(set(query_tokens)),
            1,
        )
        return coverage + phrase_bonus + (title_overlap * 0.2)

    def _tokenize(self, text: str) -> list[str]:
        """Split text into lowercase alphanumeric tokens for overlap scoring."""

        return re.findall(r"[a-z0-9]+", text.lower())


class SentenceTransformerReranker(BaseReranker):
    """Optional sentence-transformers cross-encoder reranker adapter."""

    def __init__(self, model_name: str) -> None:
        """Store the configured cross-encoder model name."""

        self._model_name = model_name
        self._model: object | None = None

    async def rerank(
        self,
        query: str,
        results: Sequence[SearchResult],
    ) -> list[SearchResult]:
        """Rerank candidates with a cross-encoder model loaded on demand."""

        model = await asyncio.to_thread(self._get_model)
        pairs = [[query, result.text] for result in results]
        scores = await asyncio.to_thread(model.predict, pairs)
        reranked_results: list[SearchResult] = []
        for result, rerank_score in zip(results, scores, strict=True):
            base_score = (
                result.fused_score
                or result.dense_score
                or result.sparse_score
                or result.score
            )
            blended_score = (base_score * 0.5) + (float(rerank_score) * 0.5)
            explanation = dict(result.explain or {})
            explanation["reranker_provider"] = "sentence-transformers"
            explanation["reranker_model"] = self._model_name
            explanation["base_score_before_rerank"] = base_score
            reranked_results.append(
                result.model_copy(
                    update={
                        "score": blended_score,
                        "rerank_score": float(rerank_score),
                        "explain": explanation,
                    }
                )
            )
        return sorted(reranked_results, key=lambda item: item.score, reverse=True)

    def _get_model(self):
        """Load and cache the cross-encoder model the first time it is needed."""

        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as error:
                msg = (
                    "sentence-transformers is required when RERANKER_PROVIDER="
                    "sentence-transformers"
                )
                raise RuntimeError(msg) from error
            self._model = CrossEncoder(self._model_name)
        return self._model


def build_reranker(settings: Settings) -> BaseReranker | None:
    """Build the configured reranker, or return none when reranking is disabled."""

    if not settings.enable_reranking:
        return None
    if settings.reranker_provider == "heuristic":
        return HeuristicReranker()
    if settings.reranker_provider == "sentence-transformers":
        return SentenceTransformerReranker(settings.reranker_model)
    return HeuristicReranker()
