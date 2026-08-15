"""Unit tests for retrieval benchmark and integration harness helpers."""

import math

from app.evaluation.retrieval_harness import (
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
