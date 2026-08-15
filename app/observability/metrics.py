"""In-memory metrics registry for request and rate-limit instrumentation."""

from __future__ import annotations

import os
import resource
import sys
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from threading import Lock


@dataclass(frozen=True, slots=True)
class LlmCallMetrics:
    """Timing and token diagnostics for one model invocation."""

    provider: str
    model: str
    trace_id: str
    status: str
    total_ms: float
    queue_wait_ms: float
    http_ms: float
    prompt_ms: float | None = None
    generation_ms: float | None = None
    overhead_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    prompt_tokens_per_second: float | None = None
    completion_tokens_per_second: float | None = None


class InMemoryMetricsRegistry:
    """Track lightweight counters and latency summaries for the running process."""

    def __init__(self) -> None:
        """Initialize the protected in-memory metric collections."""

        self._lock = Lock()
        self._requests_by_key: dict[str, int] = defaultdict(int)
        self._latency_totals_by_key: dict[str, float] = defaultdict(float)
        self._rate_limited_by_key: dict[str, int] = defaultdict(int)
        self._llm_calls_by_key: dict[str, deque[LlmCallMetrics]] = defaultdict(
            lambda: deque(maxlen=1000)
        )
        self._llm_call_counts_by_key: dict[str, int] = defaultdict(int)

    def record_request(
        self,
        scope_name: str,
        path: str,
        status_code: int,
        duration_ms: float,
    ) -> None:
        """Record one completed request and accumulate its observed latency."""

        key = self._request_key(scope_name, path, status_code)
        with self._lock:
            self._requests_by_key[key] += 1
            self._latency_totals_by_key[key] += duration_ms

    def record_rate_limit(self, scope_name: str, path: str) -> None:
        """Record that the rate limiter rejected one request."""

        key = self._scope_path_key(scope_name, path)
        with self._lock:
            self._rate_limited_by_key[key] += 1

    def record_llm_call(self, metrics: LlmCallMetrics) -> None:
        """Record a model invocation for latency diagnosis."""

        key = f"{metrics.provider}|{metrics.model}|{metrics.status}"
        with self._lock:
            self._llm_calls_by_key[key].append(metrics)
            self._llm_call_counts_by_key[key] += 1

    def snapshot(self) -> dict[str, object]:
        """Return a serializable snapshot of the collected metrics."""

        with self._lock:
            requests = []
            for key, count in sorted(self._requests_by_key.items()):
                scope_name, path, status_code_text = key.split("|", maxsplit=2)
                total_latency = self._latency_totals_by_key[key]
                requests.append(
                    {
                        "scope": scope_name,
                        "path": path,
                        "status_code": int(status_code_text),
                        "count": count,
                        "avg_latency_ms": total_latency / count,
                    }
                )
            rate_limits = []
            for key, count in sorted(self._rate_limited_by_key.items()):
                scope_name, path = key.split("|", maxsplit=1)
                rate_limits.append({"scope": scope_name, "path": path, "count": count})
            llm_calls = []
            for key, calls in sorted(self._llm_calls_by_key.items()):
                provider, model, status = key.split("|", maxsplit=2)
                llm_calls.append(
                    {
                        "provider": provider,
                        "model": model,
                        "status": status,
                        "count": self._llm_call_counts_by_key[key],
                        "recent_sample_count": len(calls),
                        "avg_total_ms": _average(calls, "total_ms"),
                        "max_total_ms": max(call.total_ms for call in calls),
                        "avg_queue_wait_ms": _average(calls, "queue_wait_ms"),
                        "avg_http_ms": _average(calls, "http_ms"),
                        "avg_prompt_ms": _average(calls, "prompt_ms"),
                        "avg_generation_ms": _average(calls, "generation_ms"),
                        "avg_overhead_ms": _average(calls, "overhead_ms"),
                        "avg_prompt_tokens": _average(calls, "prompt_tokens"),
                        "avg_completion_tokens": _average(calls, "completion_tokens"),
                        "avg_prompt_tokens_per_second": _average(
                            calls, "prompt_tokens_per_second"
                        ),
                        "avg_completion_tokens_per_second": _average(
                            calls, "completion_tokens_per_second"
                        ),
                        "latest": asdict(calls[-1]),
                    }
                )
        return {
            "requests": requests,
            "rate_limits": rate_limits,
            "llm_calls": llm_calls,
            "process": {
                "pid": os.getpid(),
                "max_rss_bytes": _max_rss_bytes(),
            },
        }

    def _request_key(self, scope_name: str, path: str, status_code: int) -> str:
        """Build the storage key for a completed request measurement."""

        return f"{scope_name}|{path}|{status_code}"

    def _scope_path_key(self, scope_name: str, path: str) -> str:
        """Build the storage key for rate-limit counters."""

        return f"{scope_name}|{path}"


def _average(calls: deque[LlmCallMetrics], field: str) -> float | None:
    """Average the available values for one metric field."""

    values = [getattr(call, field) for call in calls]
    present = [float(value) for value in values if value is not None]
    return sum(present) / len(present) if present else None


def _max_rss_bytes() -> int:
    """Return peak resident memory in bytes for the current process."""

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB; macOS reports bytes. The deployment target is Linux,
    # but keeping the branch makes local development output unsurprising.
    return int(value if sys.platform == "darwin" else value * 1024)
