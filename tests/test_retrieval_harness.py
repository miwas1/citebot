"""Unit tests for retrieval benchmark and integration harness helpers."""

import math

from app.evaluation.retrieval_harness import (
    QueryExecution,
    compare_latency_summaries,
    compare_query_results,
    compute_overlap_rate,
    summarize_latencies,
)


def test_summarize_latencies_reports_expected_percentiles() -> None:
    """Latency summaries should expose stable min, max, and percentile fields."""

    summary = summarize_latencies([10.0, 20.0, 30.0, 40.0, 50.0])

    assert summary["count"] == 5.0
    assert math.isclose(summary["mean_ms"], 30.0)
    assert math.isclose(summary["min_ms"], 10.0)
    assert math.isclose(summary["max_ms"], 50.0)
    assert math.isclose(summary["p50_ms"], 30.0)
    assert summary["p95_ms"] >= summary["p50_ms"]
    assert summary["p99_ms"] >= summary["p95_ms"]


def test_compute_overlap_rate_uses_smaller_result_window() -> None:
    """Overlap rates should be normalized against the smaller result set."""

    overlap_rate = compute_overlap_rate(["a", "b", "c"], ["b", "c"])

    assert math.isclose(overlap_rate, 1.0)


def test_compare_query_results_requires_nonzero_overlap() -> None:
    """Integration comparisons should fail when the backends share no common hits."""

    pgvector_execution = QueryExecution(
        backend="pgvector",
        query_name="citation-traceability",
        latency_ms=10.0,
        result_count=2,
        top_chunk_ids=["chunk-1", "chunk-2"],
        top_score=0.9,
        source_backend="pgvector",
    )
    qdrant_execution = QueryExecution(
        backend="qdrant",
        query_name="citation-traceability",
        latency_ms=11.0,
        result_count=2,
        top_chunk_ids=["chunk-3", "chunk-4"],
        top_score=0.88,
        source_backend="qdrant",
    )

    comparison = compare_query_results(pgvector_execution, qdrant_execution)

    assert comparison["passed"] is False
    assert math.isclose(comparison["overlap_rate"], 0.0)


def test_compare_latency_summaries_picks_lowest_p50_backend() -> None:
    """Latency comparisons should identify the backend with the faster p50."""

    comparison = compare_latency_summaries(
        {
            "pgvector": {"summary": {"p50_ms": 12.0, "p95_ms": 18.0}},
            "qdrant": {"summary": {"p50_ms": 9.5, "p95_ms": 14.0}},
        }
    )

    assert comparison["p50_winner"] == "qdrant"
    assert math.isclose(comparison["qdrant_p95_ms"], 14.0)
