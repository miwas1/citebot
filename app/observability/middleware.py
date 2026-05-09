"""Request middleware for logging, trace headers, metrics, and rate limiting."""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from time import monotonic
from typing import Deque
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import Settings
from app.observability.metrics import InMemoryMetricsRegistry

logger = logging.getLogger(__name__)


class InMemoryRateLimiter:
    """Apply a fixed-window per-client rate limit for request scopes."""

    def __init__(self, settings: Settings) -> None:
        """Store runtime settings and initialize timestamp buckets."""

        self._settings = settings
        self._buckets: dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, scope_name: str, client_key: str) -> bool:
        """Return whether the current request should be allowed for this scope."""

        limit = self._limit_for_scope(scope_name)
        if limit <= 0:
            return True
        bucket_key = f"{scope_name}|{client_key}"
        bucket = self._buckets[bucket_key]
        now = monotonic()
        window_start = now - self._settings.rate_limit_window_seconds
        while bucket and bucket[0] <= window_start:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

    def _limit_for_scope(self, scope_name: str) -> int:
        """Return the configured request limit for the given scope."""

        if scope_name == "admin":
            return self._settings.admin_rate_limit_requests
        if scope_name == "research":
            return self._settings.research_rate_limit_requests
        return 0


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Add request IDs, trace headers, logs, metrics, and rate limiting."""

    def __init__(
        self,
        app,
        settings: Settings,
        metrics_registry: InMemoryMetricsRegistry,
        rate_limiter: InMemoryRateLimiter,
    ) -> None:
        """Store the runtime collaborators used for request instrumentation."""

        super().__init__(app)
        self._settings = settings
        self._metrics_registry = metrics_registry
        self._rate_limiter = rate_limiter

    async def dispatch(self, request: Request, call_next):
        """Instrument one request and enforce per-scope rate limits."""

        request_id = request.headers.get("X-Request-ID") or uuid4().hex
        trace_id = request.headers.get("X-Trace-ID") or request_id
        request.state.request_id = request_id
        request.state.trace_id = trace_id
        scope_name = _scope_name_for_path(request.url.path, self._settings.api_prefix)
        client_key = _client_key(request)
        if not self._rate_limiter.allow(scope_name, client_key):
            self._metrics_registry.record_rate_limit(scope_name, request.url.path)
            logger.warning(
                "event=rate_limited scope=%s path=%s request_id=%s trace_id=%s client=%s",
                scope_name,
                request.url.path,
                request_id,
                trace_id,
                client_key,
            )
            response = JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded."},
            )
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Trace-ID"] = trace_id
            return response
        started = monotonic()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (monotonic() - started) * 1000
            self._metrics_registry.record_request(
                scope_name=scope_name,
                path=request.url.path,
                status_code=500,
                duration_ms=duration_ms,
            )
            logger.exception(
                "event=request_failed scope=%s method=%s path=%s status=500 duration_ms=%.2f request_id=%s trace_id=%s client=%s",
                scope_name,
                request.method,
                request.url.path,
                duration_ms,
                request_id,
                trace_id,
                client_key,
            )
            raise
        duration_ms = (monotonic() - started) * 1000
        self._metrics_registry.record_request(
            scope_name=scope_name,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Trace-ID"] = trace_id
        logger.info(
            "event=request_completed scope=%s method=%s path=%s status=%s duration_ms=%.2f request_id=%s trace_id=%s client=%s",
            scope_name,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request_id,
            trace_id,
            client_key,
        )
        return response


def _scope_name_for_path(path: str, api_prefix: str) -> str:
    """Map one request path to a coarse-grained request scope name."""

    admin_prefix = f"{api_prefix}/admin"
    research_prefix = f"{api_prefix}/research"
    if path.startswith(admin_prefix):
        return "admin"
    if path.startswith(research_prefix):
        return "research"
    return "public"


def _client_key(request: Request) -> str:
    """Return a stable per-client identifier for rate-limit bucketing."""

    if request.client and request.client.host:
        return request.client.host
    return "unknown"
