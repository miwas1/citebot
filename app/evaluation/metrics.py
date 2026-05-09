"""Deterministic evaluation metrics for retrieval, citations, and answer fit."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from urllib.parse import unquote, urlparse

from app.agents.schemas import ResearchResponse
from app.evaluation.schemas import EvaluationCase, EvaluationCaseResult


def compute_case_metrics(
    case: EvaluationCase,
    response: ResearchResponse,
) -> dict[str, float]:
    """Compute deterministic metrics for one evaluated research response."""

    metrics: dict[str, float] = {}
    relevant_positions = _relevant_positions(case, response)
    relevant_expectation_count = _relevant_expectation_count(case)
    if relevant_expectation_count > 0:
        metrics["hit_rate_at_k"] = 1.0 if relevant_positions else 0.0
        metrics["mrr_at_k"] = 1.0 / relevant_positions[0] if relevant_positions else 0.0
        metrics["context_precision"] = _safe_ratio(
            len(relevant_positions),
            len(response.retrieved_contexts),
            default=0.0,
        )
        retrieved_relevant_units = _retrieved_relevant_units(case, response)
        metrics["context_recall"] = _safe_ratio(
            len(retrieved_relevant_units),
            relevant_expectation_count,
            default=0.0,
        )
        metrics["ndcg_at_k"] = _compute_ndcg(
            relevant_positions,
            min(relevant_expectation_count, len(response.retrieved_contexts)),
        )
    citation_chunk_ids = {citation.chunk_id for citation in response.answer.citations}
    retrieved_chunk_ids = {context.chunk_id for context in response.retrieved_contexts}
    metrics["citation_retrieval_precision"] = _safe_ratio(
        len(citation_chunk_ids & retrieved_chunk_ids),
        len(citation_chunk_ids),
        default=1.0,
    )
    metrics["citation_traceability_rate"] = _safe_ratio(
        sum(
            1
            for citation in response.answer.citations
            if citation.chunk_id and citation.document_id and citation.source_uri
        ),
        len(response.answer.citations),
        default=1.0,
    )
    verification_claims = response.verification.claims
    metrics["verification_pass_rate"] = _safe_ratio(
        sum(1 for claim in verification_claims if claim.verdict != "unsupported"),
        len(verification_claims),
        default=1.0,
    )
    metrics["unsupported_claim_ratio"] = _safe_ratio(
        sum(1 for claim in verification_claims if claim.verdict == "unsupported"),
        len(verification_claims),
        default=0.0,
    )
    if case.reference_answer:
        metrics["reference_overlap_f1"] = token_overlap_f1(
            case.reference_answer,
            response.answer.direct_answer,
        )
    if case.expected_answer_traits:
        metrics["trait_coverage"] = _trait_coverage(
            case.expected_answer_traits,
            response.answer.direct_answer,
            response.answer.supporting_evidence,
            response.answer.limitations,
        )
    return metrics


def aggregate_metrics(case_results: Sequence[EvaluationCaseResult]) -> dict[str, float]:
    """Average numeric metrics across all completed evaluation cases."""

    metric_values: dict[str, list[float]] = {}
    for case_result in case_results:
        for name, value in case_result.metrics.items():
            metric_values.setdefault(name, []).append(value)
    return {
        name: sum(values) / len(values)
        for name, values in metric_values.items()
        if values
    }


def token_overlap_f1(reference_text: str, answer_text: str) -> float:
    """Return a simple token-overlap F1 score between reference and answer text."""

    reference_tokens = _tokenize(reference_text)
    answer_tokens = _tokenize(answer_text)
    if not reference_tokens or not answer_tokens:
        return 0.0
    reference_counts = _count_tokens(reference_tokens)
    answer_counts = _count_tokens(answer_tokens)
    overlap = sum(
        min(reference_counts[token], answer_counts.get(token, 0))
        for token in reference_counts
    )
    precision = overlap / len(answer_tokens)
    recall = overlap / len(reference_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _tokenize(text: str) -> list[str]:
    """Split free text into lowercase alphanumeric tokens."""

    return re.findall(r"[a-z0-9]+", text.lower())


def _count_tokens(tokens: Iterable[str]) -> dict[str, int]:
    """Count token occurrences for overlap-based string scoring."""

    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    return counts


def _trait_coverage(
    expected_traits: Sequence[str],
    direct_answer: str,
    supporting_evidence: Sequence[str],
    limitations: str | None,
) -> float:
    """Measure how many expected answer traits appear in the returned answer."""

    search_space = " ".join(
        [direct_answer, *supporting_evidence, limitations or ""]
    ).lower()
    matched = sum(1 for trait in expected_traits if trait.lower() in search_space)
    return _safe_ratio(matched, len(expected_traits), default=1.0)


def _relevant_positions(
    case: EvaluationCase,
    response: ResearchResponse,
) -> list[int]:
    """Return one-based retrieval ranks that satisfy the case relevance constraints."""

    positions: list[int] = []
    for index, context in enumerate(response.retrieved_contexts, start=1):
        if _is_relevant_context(
            case, context.chunk_id, context.document_id, context.source_uri
        ):
            positions.append(index)
    return positions


def _retrieved_relevant_units(
    case: EvaluationCase,
    response: ResearchResponse,
) -> set[str]:
    """Return the expected relevance units recovered by retrieval."""

    relevant_units: set[str] = set()
    normalized_expected_source_uris = {
        _normalize_source_value(source_uri) for source_uri in case.expected_source_uris
    }
    for context in response.retrieved_contexts:
        if context.chunk_id in case.expected_chunk_ids:
            relevant_units.add(f"chunk:{context.chunk_id}")
        if context.document_id in case.expected_document_ids:
            relevant_units.add(f"document:{context.document_id}")
        if (
            _normalize_source_value(context.source_uri)
            in normalized_expected_source_uris
        ):
            relevant_units.add(f"source:{context.source_uri}")
    return relevant_units


def _relevant_expectation_count(case: EvaluationCase) -> int:
    """Return the number of explicit retrieval expectations for a case."""

    return (
        len(case.expected_chunk_ids)
        + len(case.expected_document_ids)
        + len(case.expected_source_uris)
    )


def _is_relevant_context(
    case: EvaluationCase,
    chunk_id: str,
    document_id: str,
    source_uri: str,
) -> bool:
    """Return whether one retrieved context satisfies any explicit expectation."""

    normalized_source_uri = _normalize_source_value(source_uri)
    normalized_expected_source_uris = {
        _normalize_source_value(expected_source_uri)
        for expected_source_uri in case.expected_source_uris
    }
    return (
        chunk_id in case.expected_chunk_ids
        or document_id in case.expected_document_ids
        or normalized_source_uri in normalized_expected_source_uris
    )


def _normalize_source_value(source_uri: str) -> str:
    """Normalize local file URIs and file paths to a comparable form."""

    parsed = urlparse(source_uri)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).as_posix()
    if parsed.scheme:
        return source_uri
    return Path(source_uri).as_posix()


def _compute_ndcg(relevant_positions: Sequence[int], ideal_hits: int) -> float:
    """Compute a binary-relevance nDCG score from relevant retrieval ranks."""

    if not relevant_positions or ideal_hits <= 0:
        return 0.0
    dcg = sum(1.0 / math.log2(position + 1) for position in relevant_positions)
    idcg = sum(1.0 / math.log2(position + 1) for position in range(1, ideal_hits + 1))
    if idcg == 0:
        return 0.0
    return dcg / idcg


def _safe_ratio(numerator: int, denominator: int, default: float) -> float:
    """Divide two integers with a stable default for zero denominators."""

    if denominator == 0:
        return default
    return numerator / denominator
