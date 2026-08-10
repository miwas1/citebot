"""Bounded admission control for expensive interactive workloads."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class AdmissionRejected(RuntimeError):
    """Raised when a resource-intensive request cannot enter the bounded queue."""


class BoundedAdmission:
    """Limit active work and bound the number of requests waiting for it."""

    def __init__(self, concurrency: int, queue_size: int, timeout_seconds: float) -> None:
        self._semaphore = asyncio.Semaphore(concurrency)
        self._max_pending = concurrency + queue_size
        self._timeout_seconds = timeout_seconds
        self._waiting = 0

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[None]:
        """Acquire capacity or fail quickly instead of retaining an unbounded request."""

        if self._waiting >= self._max_pending:
            raise AdmissionRejected("Research capacity is busy; retry shortly.")
        self._waiting += 1
        acquired = False
        try:
            try:
                await asyncio.wait_for(
                    self._semaphore.acquire(),
                    timeout=self._timeout_seconds,
                )
                acquired = True
            except TimeoutError as error:
                raise AdmissionRejected(
                    "Research capacity is busy; retry shortly."
                ) from error
            yield
        finally:
            self._waiting -= 1
            if acquired:
                self._semaphore.release()
