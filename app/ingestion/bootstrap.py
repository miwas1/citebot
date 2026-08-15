"""First-run bootstrap for the bundled offline sample corpus."""

from __future__ import annotations

import fcntl
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.core.config import Settings
from app.ingestion.repository import IngestionRepository
from app.ingestion.schemas import JobStatusResponse
from app.ingestion.service import IngestionService
from app.projects.service import SAMPLE_PROJECT_ID

logger = logging.getLogger(__name__)
SAMPLE_CORPUS_DOCUMENT_COUNT = 100


async def ensure_sample_corpus(
    settings: Settings,
    repository: IngestionRepository,
    ingestion_service: IngestionService,
) -> JobStatusResponse | None:
    """Queue or ingest the bundled corpus exactly once on an offline first run."""

    if not settings.sample_corpus_auto_ingest:
        return None
    if settings.runtime_mode != "offline":
        logger.info(
            "sample_corpus_bootstrap_skipped runtime_mode=%s",
            settings.runtime_mode,
        )
        return None
    source_path = settings.sample_corpus_path
    if not source_path.exists():
        logger.warning("sample_corpus_bootstrap_missing path=%s", source_path)
        return None

    lock_path = settings.object_storage_path.parent / ".sample-corpus-bootstrap.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _process_lock(lock_path) as acquired:
        if not acquired:
            logger.info("sample_corpus_bootstrap_in_progress")
            return None
        try:
            already_scheduled = await repository.has_active_or_completed_job(
                str(source_path.resolve()),
                SAMPLE_PROJECT_ID,
                SAMPLE_CORPUS_DOCUMENT_COUNT,
            )
        except TypeError:
            # Compatibility with small test/dry-run repository adapters.
            already_scheduled = await repository.has_active_or_completed_job(
                str(source_path.resolve())
            )
        if already_scheduled:
            logger.info("sample_corpus_bootstrap_already_scheduled path=%s", source_path)
            return None
        if settings.ingestion_execution_mode == "queued":
            job = await ingestion_service.enqueue_path(
                source_path,
                project_id=SAMPLE_PROJECT_ID,
                embedding_version=settings.embedding_version,
                index_version="v2",
            )
        else:
            job = await ingestion_service.ingest_path(
                source_path,
                project_id=SAMPLE_PROJECT_ID,
                embedding_version=settings.embedding_version,
                index_version="v2",
            )
        logger.info(
            "sample_corpus_bootstrap_scheduled job_id=%s mode=%s path=%s",
            job.job_id,
            settings.ingestion_execution_mode,
            source_path,
        )
        return job


@contextmanager
def _process_lock(path: Path) -> Iterator[bool]:
    """Acquire a non-blocking host/container-shared lock for bootstrap scheduling."""

    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
