"""Observability helpers for request metrics, logging, and middleware."""

from __future__ import annotations

import logging

from app.core.config import Settings
from app.observability.metrics import InMemoryMetricsRegistry
from app.observability.middleware import (InMemoryRateLimiter,
                                          ObservabilityMiddleware)

_LOGGING_CONFIGURED = False


def configure_logging(settings: Settings) -> None:
	"""Configure a simple structured log format for the current process."""

	global _LOGGING_CONFIGURED
	if _LOGGING_CONFIGURED:
		return
	logging.basicConfig(
		level=getattr(logging, settings.observability_log_level, logging.INFO),
		format="timestamp=%(asctime)s level=%(levelname)s name=%(name)s message=%(message)s",
	)
	_LOGGING_CONFIGURED = True


__all__ = [
	"InMemoryMetricsRegistry",
	"InMemoryRateLimiter",
	"ObservabilityMiddleware",
	"configure_logging",
]
]
