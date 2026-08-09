"""Durable local ingestion worker entrypoint."""

from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.core.lifecycle import build_container

logger = logging.getLogger(__name__)


async def run_worker() -> None:
    """Run the SQLite-backed ingestion queue until interrupted."""

    settings = get_settings()
    container = build_container(settings)
    await container.initialize()
    try:
        await container.ingestion_service.recover_stale_jobs()
        while True:
            try:
                job = await container.ingestion_service.run_next_job(
                    settings.ingestion_worker_id
                )
                if job is None:
                    await asyncio.sleep(settings.queue_poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ingestion worker job failed")
                await asyncio.sleep(settings.queue_poll_seconds)
    finally:
        await container.close()


def main() -> None:
    """Start the worker event loop."""

    logging.basicConfig(level=get_settings().observability_log_level)
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("Ingestion worker stopped")


if __name__ == "__main__":
    main()
