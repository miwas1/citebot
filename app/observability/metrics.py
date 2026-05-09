"""In-memory metrics registry for request and rate-limit instrumentation."""

from __future__ import annotations

from collections import defaultdict
from threading import Lock


class InMemoryMetricsRegistry:
    """Track lightweight counters and latency summaries for the running process."""

    def __init__(self) -> None:
        """Initialize the protected in-memory metric collections."""

        self._lock = Lock()
        self._requests_by_key: dict[str, int] = defaultdict(int)
        self._latency_totals_by_key: dict[str, float] = defaultdict(float)
        self._rate_limited_by_key: dict[str, int] = defaultdict(int)

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
        return {
            "requests": requests,
            "rate_limits": rate_limits,
        }

    def _request_key(self, scope_name: str, path: str, status_code: int) -> str:
        """Build the storage key for a completed request measurement."""

        return f"{scope_name}|{path}|{status_code}"

    def _scope_path_key(self, scope_name: str, path: str) -> str:
        """Build the storage key for rate-limit counters."""

        return f"{scope_name}|{path}"
