"""Tests for the first-run sample corpus bootstrap."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.config import Settings
from app.ingestion.bootstrap import ensure_sample_corpus
from app.ingestion.schemas import JobStatusResponse


@pytest.mark.asyncio
async def test_sample_corpus_bootstrap_is_idempotent(tmp_path: Path) -> None:
    """Only one durable bootstrap job is created for a fresh installation."""

    sample_path = tmp_path / "sample_corpus"
    sample_path.mkdir()
    settings = Settings(
        RUNTIME_MODE="offline",
        SAMPLE_CORPUS_PATH=sample_path,
        SAMPLE_CORPUS_AUTO_INGEST=True,
        OBJECT_STORAGE_PATH=tmp_path / "storage" / "raw_documents",
        INGESTION_EXECUTION_MODE="queued",
        EVALUATION_EVALUATOR_PROVIDER="local",
    )

    class Repository:
        def __init__(self) -> None:
            self.scheduled = False

        async def has_active_or_completed_job(self, source_path: str) -> bool:
            return self.scheduled

    class Ingestion:
        def __init__(self, repository: Repository) -> None:
            self.repository = repository
            self.calls = 0

        async def enqueue_path(self, source_path, **kwargs):
            self.calls += 1
            self.repository.scheduled = True
            return JobStatusResponse(
                job_id="sample-job",
                source_path=str(source_path),
                status="queued",
                force_reindex=False,
                embedding_version=kwargs["embedding_version"],
                index_version=kwargs["index_version"],
                started_at=datetime.now(UTC),
            )

    repository = Repository()
    ingestion = Ingestion(repository)
    first = await ensure_sample_corpus(settings, repository, ingestion)
    second = await ensure_sample_corpus(settings, repository, ingestion)

    assert first is not None
    assert second is None
    assert ingestion.calls == 1
